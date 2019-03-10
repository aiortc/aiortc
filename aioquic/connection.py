import binascii
import logging
import os

from aioquic import tls
from aioquic.crypto import CryptoPair
from aioquic.packet import (PACKET_FIXED_BIT, PACKET_TYPE_HANDSHAKE,
                            PACKET_TYPE_INITIAL, PROTOCOL_VERSION_DRAFT_17, QuicFrameType,
                            QuicHeader, pull_ack_frame, pull_crypto_frame,
                            pull_new_connection_id_frame, pull_quic_header,
                            pull_uint_var, push_ack_frame, push_crypto_frame,
                            push_quic_header, push_uint_var)
from aioquic.rangeset import RangeSet
from aioquic.tls import Buffer

logger = logging.getLogger('quic')


CLIENT_QUIC_TRANSPORT_PARAMETERS = binascii.unhexlify(
    b'ff0000110031000500048010000000060004801000000007000480100000000'
    b'4000481000000000100024258000800024064000a00010a')
SERVER_QUIC_TRANSPORT_PARAMETERS = binascii.unhexlify(
    b'ff00001104ff000011004500050004801000000006000480100000000700048'
    b'010000000040004810000000001000242580002001000000000000000000000'
    b'000000000000000800024064000a00010a')

PACKET_MAX_SIZE = 1280
SEND_PN_SIZE = 2


def get_epoch(packet_type):
    if packet_type == PACKET_TYPE_INITIAL:
        return tls.Epoch.INITIAL
    elif packet_type == PACKET_TYPE_HANDSHAKE:
        return tls.Epoch.HANDSHAKE
    else:
        return tls.Epoch.ONE_RTT


class PacketSpace:
    def __init__(self):
        self.ack_queue = RangeSet()
        self.crypto = CryptoPair()


class QuicConnection:
    def __init__(self, is_client=True, certificate=None, private_key=None):
        if not is_client:
            assert certificate is not None, 'SSL certificate is required'
            assert private_key is not None, 'SSL private key is required'

        self.is_client = is_client
        self.host_cid = os.urandom(8)
        self.peer_cid = os.urandom(8)
        self.peer_cid_set = False
        self.tls = tls.Context(is_client=is_client)
        if is_client:
            self.tls.handshake_extensions.append(
                (tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS, CLIENT_QUIC_TRANSPORT_PARAMETERS),
            )
        else:
            self.tls.handshake_extensions.append(
                (tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS, SERVER_QUIC_TRANSPORT_PARAMETERS),
            )
            self.tls.certificate = certificate
            self.tls.certificate_private_key = private_key

        self.tls.update_traffic_key_cb = self._update_traffic_key

        self.send_ack = {
            tls.Epoch.INITIAL: False,
            tls.Epoch.HANDSHAKE: False,
            tls.Epoch.ONE_RTT: False,
        }
        self.send_buffer = {
            tls.Epoch.INITIAL: Buffer(capacity=4096),
            tls.Epoch.HANDSHAKE: Buffer(capacity=4096),
            tls.Epoch.ONE_RTT: Buffer(capacity=4096),
        }
        self.spaces = {
            tls.Epoch.INITIAL: PacketSpace(),
            tls.Epoch.HANDSHAKE: PacketSpace(),
            tls.Epoch.ONE_RTT: PacketSpace(),
        }

        self.crypto_initialized = False
        self.packet_number = 0

    def connection_made(self):
        if self.is_client:
            self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(cid=self.peer_cid,
                                                                is_client=self.is_client)
            self.crypto_initialized = True

            self.tls.handle_message(b'', self.send_buffer)

    def datagram_received(self, data: bytes):
        """
        Handle an incoming datagram.
        """
        buf = Buffer(data=data)

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))
            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            tls.pull_bytes(buf, header.rest_length)

            if not self.is_client and not self.crypto_initialized:
                self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(cid=header.destination_cid,
                                                                    is_client=self.is_client)
                self.crypto_initialized = True

            epoch = get_epoch(header.packet_type)
            space = self.spaces[epoch]
            plain_header, plain_payload, packet_number = space.crypto.decrypt_packet(
                data[start_off:end_off], encrypted_off)

            if not self.peer_cid_set:
                self.peer_cid = header.source_cid
                self.peer_cid_set = True

            # record packet as received
            is_ack_only = self._payload_received(plain_payload)
            space.ack_queue.add(packet_number)
            if not is_ack_only:
                self.send_ack[epoch] = True

    def pending_datagrams(self):
        """
        Retrieve outgoing datagrams.
        """
        for epoch in [tls.Epoch.INITIAL, tls.Epoch.HANDSHAKE]:
            yield from self._write_handshake(epoch)

        yield from self._write_application()

    def _payload_received(self, plain):
        buf = Buffer(data=plain)

        is_ack_only = True
        while not buf.eof():
            frame_type = pull_uint_var(buf)
            if frame_type in [QuicFrameType.PADDING, QuicFrameType.PING]:
                is_ack_only = False
            elif frame_type == QuicFrameType.ACK:
                pull_ack_frame(buf)
            elif frame_type == QuicFrameType.CRYPTO:
                is_ack_only = False
                offset, data = pull_crypto_frame(buf)
                assert len(data)
                self.tls.handle_message(data, self.send_buffer)
            elif frame_type == QuicFrameType.NEW_CONNECTION_ID:
                is_ack_only = False
                pull_new_connection_id_frame(buf)
            else:
                logger.warning('unhandled frame type %d', frame_type)
                break
        return is_ack_only

    def _update_traffic_key(self, direction, epoch, secret):
        crypto = self.spaces[epoch].crypto
        if direction == tls.Direction.ENCRYPT:
            crypto.send.setup(self.tls.key_schedule.algorithm, secret)
        else:
            crypto.recv.setup(self.tls.key_schedule.algorithm, secret)

    def _write_application(self):
        epoch = tls.Epoch.ONE_RTT
        space = self.spaces[epoch]
        send_ack = space.ack_queue if self.send_ack[epoch] else False
        if not space.crypto.send.is_valid() or not send_ack:
            return

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        # write header
        tls.push_uint8(buf, PACKET_FIXED_BIT | (SEND_PN_SIZE - 1))
        tls.push_bytes(buf, self.peer_cid)
        tls.push_uint16(buf, self.packet_number)
        header_size = buf.tell()

        # ACK
        if send_ack:
            push_uint_var(buf, QuicFrameType.ACK)
            push_ack_frame(buf, send_ack, 0)
            self.send_ack[epoch] = False

        # encrypt
        packet_size = buf.tell()
        data = buf.data
        yield space.crypto.encrypt_packet(data[0:header_size], data[header_size:packet_size])

        self.packet_number += 1

    def _write_handshake(self, epoch):
        space = self.spaces[epoch]
        send_ack = space.ack_queue if self.send_ack[epoch] else False
        send_data = self.send_buffer[epoch].data
        self.send_buffer[epoch].seek(0)
        if not space.crypto.send.is_valid() or (not send_ack and not send_data):
            return

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        if epoch == tls.Epoch.INITIAL:
            packet_type = PACKET_TYPE_INITIAL
        else:
            packet_type = PACKET_TYPE_HANDSHAKE

        # write header
        push_quic_header(buf, QuicHeader(
            version=PROTOCOL_VERSION_DRAFT_17,
            packet_type=packet_type | (SEND_PN_SIZE - 1),
            destination_cid=self.peer_cid,
            source_cid=self.host_cid,
        ))
        header_size = buf.tell()

        if send_data:
            # CRYPTO
            push_uint_var(buf, QuicFrameType.CRYPTO)
            with push_crypto_frame(buf):
                tls.push_bytes(buf, send_data)

            # PADDING
            if epoch == tls.Epoch.INITIAL:
                tls.push_bytes(
                    buf,
                    bytes(PACKET_MAX_SIZE - space.crypto.aead_tag_size - buf.tell()))

        # ACK
        if send_ack:
            push_uint_var(buf, QuicFrameType.ACK)
            push_ack_frame(buf, send_ack, 0)
            self.send_ack[epoch] = False

        # finalize length
        packet_size = buf.tell()
        buf.seek(header_size - SEND_PN_SIZE - 2)
        length = packet_size - header_size + 2 + space.crypto.aead_tag_size
        tls.push_uint16(buf, length | 0x4000)
        tls.push_uint16(buf, self.packet_number)
        buf.seek(packet_size)

        # encrypt
        data = buf.data
        yield space.crypto.encrypt_packet(data[0:header_size], data[header_size:packet_size])

        self.packet_number += 1
