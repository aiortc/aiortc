import binascii
import logging
import os
from enum import IntEnum

from aioquic import tls
from aioquic.crypto import CryptoPair
from aioquic.packet import (PACKET_TYPE_HANDSHAKE, PACKET_TYPE_INITIAL,
                            PROTOCOL_VERSION_DRAFT_17, QuicHeader,
                            pull_ack_frame, pull_crypto_frame,
                            pull_quic_header, pull_uint_var, push_ack_frame,
                            push_crypto_frame, push_quic_header, push_uint_var)
from aioquic.rangeset import RangeSet
from aioquic.tls import Buffer

logger = logging.getLogger('quic')


CLIENT_QUIC_TRANSPORT_PARAMETERS = binascii.unhexlify(
    b'ff0000110031000500048010000000060004801000000007000480100000000'
    b'4000481000000000100024258000800024064000a00010a')
PACKET_MAX_SIZE = 1280
SEND_PN_SIZE = 2


def get_epoch(packet_type):
    if packet_type == PACKET_TYPE_INITIAL:
        return tls.Epoch.INITIAL
    elif packet_type == PACKET_TYPE_HANDSHAKE:
        return tls.Epoch.HANDSHAKE
    else:
        return tls.Epoch.ONE_RTT


class QuicFrameType(IntEnum):
    PADDING = 0
    PING = 1
    ACK = 2
    ACK_WITH_ECN = 3
    RESET_STREAM = 4
    STOP_SENDING = 5
    CRYPTO = 6


class QuicConnection:
    def __init__(self):
        self.host_cid = b''
        self.peer_cid = os.urandom(8)
        self.tls = tls.Context(is_client=True)
        self.tls.handshake_extensions.append(
            (tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS, CLIENT_QUIC_TRANSPORT_PARAMETERS),
        )
        self.tls.update_traffic_key_cb = self.update_traffic_key

        self.send_buffer = Buffer(capacity=4096)
        self.send_ack = False
        self.send_datagrams = []
        self.send_padding = True

        self.ack = {
            tls.Epoch.INITIAL: RangeSet(),
            tls.Epoch.HANDSHAKE: RangeSet(),
            tls.Epoch.ONE_RTT: RangeSet(),
        }
        self.crypto = {
            tls.Epoch.INITIAL: CryptoPair.initial(cid=self.peer_cid, is_client=True),
            tls.Epoch.HANDSHAKE: CryptoPair(),
            tls.Epoch.ONE_RTT: CryptoPair(),
        }
        self.packet_number = {
            tls.Epoch.INITIAL: 0,
            tls.Epoch.HANDSHAKE: 0,
            tls.Epoch.ONE_RTT: 0,
        }

    def connection_made(self):
        self.tls.handle_message(b'', self.send_buffer)
        self.write_crypto()

    def datagram_received(self, data):
        buf = Buffer(data=data)

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))
            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            tls.pull_bytes(buf, header.rest_length)

            epoch = get_epoch(header.packet_type)
            crypto = self.crypto[epoch]
            plain_header, plain_payload, packet_number = crypto.recv.decrypt_packet(
                data[start_off:end_off], encrypted_off)

            self.peer_cid = header.source_cid
            is_ack_only = self.payload_received(plain_payload)
            self.ack[epoch].add(packet_number)
            if not is_ack_only:
                self.send_ack = True
        self.write_crypto()

    def payload_received(self, plain):
        buf = Buffer(data=plain)

        is_ack_only = True
        while not buf.eof():
            frame_type = pull_uint_var(buf)
            if frame_type == QuicFrameType.PADDING:
                pass
            elif frame_type == QuicFrameType.ACK:
                pull_ack_frame(buf)
            elif frame_type == QuicFrameType.CRYPTO:
                is_ack_only = False
                data = pull_crypto_frame(buf)
                assert len(data)
                self.tls.handle_message(data, self.send_buffer)
            else:
                logger.warning('unhandled frame type %d', frame_type)
                break
        return is_ack_only

    def write_crypto(self):
        if not self.send_buffer.tell():
            return

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        if self.send_padding:
            packet_type = PACKET_TYPE_INITIAL
        else:
            packet_type = PACKET_TYPE_HANDSHAKE
        epoch = get_epoch(packet_type)
        crypto = self.crypto[epoch]

        # write header
        push_quic_header(buf, QuicHeader(
            version=PROTOCOL_VERSION_DRAFT_17,
            packet_type=packet_type | (SEND_PN_SIZE - 1),
            destination_cid=self.peer_cid,
            source_cid=self.host_cid,
        ))
        header_size = buf.tell()

        # CRYPTO
        push_uint_var(buf, QuicFrameType.CRYPTO)
        with push_crypto_frame(buf):
            tls.push_bytes(buf, self.send_buffer.data)
            self.send_buffer.seek(0)

        # PADDING
        if self.send_padding:
            tls.push_bytes(
                buf,
                bytes(PACKET_MAX_SIZE - crypto.send.aead_tag_size - buf.tell()))
            self.send_padding = False

        # ACK
        if self.send_ack:
            push_uint_var(buf, QuicFrameType.ACK)
            push_ack_frame(buf, self.ack[epoch], 0)
            self.send_ack = False

        # finalize length
        packet_size = buf.tell()
        buf.seek(header_size - SEND_PN_SIZE - 2)
        length = packet_size - header_size + 2 + crypto.send.aead_tag_size
        tls.push_uint16(buf, length | 0x4000)
        tls.push_uint16(buf, self.packet_number[epoch])
        buf.seek(packet_size)

        # encrypt
        data = buf.data
        self.send_datagrams.append(
            crypto.send.encrypt_packet(data[0:header_size], data[header_size:packet_size]))

        self.packet_number[epoch] += 1

    def update_traffic_key(self, direction, epoch, secret):
        crypto = self.crypto[epoch]
        if direction == tls.Direction.ENCRYPT:
            crypto.send.setup(self.tls.key_schedule.algorithm, secret)
        else:
            crypto.recv.setup(self.tls.key_schedule.algorithm, secret)
