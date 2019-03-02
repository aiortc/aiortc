import binascii
import logging
import os
from enum import IntEnum

from aioquic import tls
from aioquic.crypto import CryptoContext
from aioquic.packet import (PACKET_TYPE_HANDSHAKE, PACKET_TYPE_INITIAL,
                            PROTOCOL_VERSION_DRAFT_17, QuicHeader,
                            pull_ack_frame, pull_crypto_frame,
                            pull_quic_header, pull_uint_var, push_crypto_frame,
                            push_quic_header, push_uint_var)
from aioquic.tls import Buffer

logger = logging.getLogger('quic')


PACKET_MAX_SIZE = 1280
SEND_PN_SIZE = 2


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
        self.local_cid = b''
        self.peer_cid = os.urandom(8)
        self.packet_number = 0
        self.tls = tls.Context(is_client=True)
        self.tls.update_traffic_key_cb = self.update_traffic_key

        self.send_buffer = Buffer(capacity=4096)
        self.send_ack = False
        self.send_datagrams = []
        self.send_padding = True
        self.send_crypto = CryptoContext(self.peer_cid, is_client=True)
        self.recv_crypto = CryptoContext(self.peer_cid, is_client=False)

    def connection_made(self):
        self.tls.handle_message(b'', self.send_buffer)
        self.write_crypto()

    def datagram_received(self, data):
        buf = Buffer(data=data)

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.peer_cid))
            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            tls.pull_bytes(buf, header.rest_length)
            plain_header, plain_payload = self.recv_crypto.decrypt_packet(
                data[start_off:end_off], encrypted_off)
            self.peer_cid = header.source_cid
            self.send_ack = True
            self.payload_received(plain_payload)

    def payload_received(self, plain):
        buf = Buffer(data=plain)

        while not buf.eof():
            frame_type = pull_uint_var(buf)
            if frame_type == QuicFrameType.ACK:
                pull_ack_frame(buf)
            elif frame_type == QuicFrameType.CRYPTO:
                data = pull_crypto_frame(buf)
                assert len(data)
                self.tls.handle_message(data, self.send_buffer)
                self.write_crypto()
            else:
                logger.warning('unhandled frame type %d', frame_type)
                break

    def write_crypto(self):
        if not self.send_buffer.tell():
            return

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        if self.send_padding:
            packet_type = PACKET_TYPE_INITIAL
        else:
            packet_type = PACKET_TYPE_HANDSHAKE

        # write header
        push_quic_header(buf, QuicHeader(
            version=PROTOCOL_VERSION_DRAFT_17,
            packet_type=packet_type | (SEND_PN_SIZE - 1),
            destination_cid=self.peer_cid,
            source_cid=self.local_cid,
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
                bytes(PACKET_MAX_SIZE - self.send_crypto.aead_tag_size - buf.tell()))
            self.send_padding = False

        # ACK
        if self.send_ack:
            push_uint_var(buf, QuicFrameType.ACK)
            tls.push_bytes(buf, binascii.unhexlify('01000001'))
            self.send_ack = False

        # finalize length
        packet_size = buf.tell()
        buf.seek(header_size - SEND_PN_SIZE - 2)
        length = packet_size - header_size + 2 + self.send_crypto.aead_tag_size
        tls.push_uint16(buf, length | 0x4000)
        tls.push_uint16(buf, self.packet_number)
        buf.seek(packet_size)

        # encrypt
        data = buf.data
        self.send_datagrams.append(
            self.send_crypto.encrypt_packet(data[0:header_size], data[header_size:packet_size]))

        # FIXME: when do we raise packet number?
        # self.packet_number += 1

    def update_traffic_key(self, direction, epoch, secret):
        if epoch == tls.Epoch.ONE_RTT:
            return
        if direction == tls.Direction.ENCRYPT:
            self.send_crypto.setup(self.tls.key_schedule.algorithm, secret)
        else:
            self.recv_crypto.setup(self.tls.key_schedule.algorithm, secret)
