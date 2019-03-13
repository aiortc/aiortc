import logging
import os

from . import tls
from .crypto import CryptoPair
from .packet import (PACKET_FIXED_BIT, PACKET_TYPE_HANDSHAKE,
                     PACKET_TYPE_INITIAL, PROTOCOL_VERSION_DRAFT_17,
                     QuicFrameType, QuicHeader, QuicTransportParameters,
                     pull_ack_frame, pull_crypto_frame,
                     pull_new_connection_id_frame, pull_quic_header,
                     pull_uint_var, push_ack_frame, push_crypto_frame,
                     push_quic_header, push_quic_transport_parameters,
                     push_uint_var)
from .rangeset import RangeSet
from .stream import QuicStream
from .tls import Buffer

logger = logging.getLogger('quic')

PACKET_MAX_SIZE = 1280
SECRETS_LABELS = [
    [None, 'QUIC_CLIENT_EARLY_TRAFFIC_SECRET', 'QUIC_CLIENT_HANDSHAKE_TRAFFIC_SECRET',
     'QUIC_CLIENT_TRAFFIC_SECRET_0'],
    [None, None, 'QUIC_SERVER_HANDSHAKE_TRAFFIC_SECRET', 'QUIC_SERVER_TRAFFIC_SECRET_0']
]
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
    def __init__(self, is_client=True, certificate=None, private_key=None, secrets_log_file=None):
        if not is_client:
            assert certificate is not None, 'SSL certificate is required'
            assert private_key is not None, 'SSL private key is required'

        self.is_client = is_client
        self.host_cid = os.urandom(8)
        self.peer_cid = os.urandom(8)
        self.peer_cid_set = False
        self.secrets_log_file = secrets_log_file
        self.tls = tls.Context(is_client=is_client)

        self.quic_transport_parameters = QuicTransportParameters(
            idle_timeout=600,
            initial_max_data=16777216,
            initial_max_stream_data_bidi_local=1048576,
            initial_max_stream_data_bidi_remote=1048576,
            initial_max_stream_data_uni=1048576,
            initial_max_streams_bidi=100,
            ack_delay_exponent=10,
        )

        if is_client:
            self.quic_transport_parameters.initial_version = PROTOCOL_VERSION_DRAFT_17
        else:
            self.quic_transport_parameters.negotiated_version = PROTOCOL_VERSION_DRAFT_17
            self.quic_transport_parameters.supported_versions = [PROTOCOL_VERSION_DRAFT_17]
            self.quic_transport_parameters.stateless_reset_token = bytes(16)
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
        self.streams = {
            tls.Epoch.INITIAL: QuicStream(),
            tls.Epoch.HANDSHAKE: QuicStream(),
            tls.Epoch.ONE_RTT: QuicStream(),
        }

        self.crypto_initialized = False
        self.packet_number = 0

    def connection_made(self):
        if self.is_client:
            self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(cid=self.peer_cid,
                                                                is_client=self.is_client)
            self.crypto_initialized = True

            self.tls.handshake_extensions.append(
                (tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS, self._serialize_parameters()),
            )
            self.tls.handle_message(b'', self.send_buffer)

    def datagram_received(self, data: bytes):
        """
        Handle an incoming datagram.
        """
        buf = Buffer(data=data)

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))
            if header.packet_type is None:
                versions = []
                while not buf.eof():
                    versions.append('0x%x' % tls.pull_uint32(buf))
                raise Exception('Version negotiation needed: %s' % versions)

            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            tls.pull_bytes(buf, header.rest_length)

            if not self.is_client and not self.crypto_initialized:
                self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(cid=header.destination_cid,
                                                                    is_client=self.is_client)
                self.crypto_initialized = True

                self.tls.handshake_extensions.append(
                    (tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS, self._serialize_parameters()),
                )

            epoch = get_epoch(header.packet_type)
            space = self.spaces[epoch]
            plain_header, plain_payload, packet_number = space.crypto.decrypt_packet(
                data[start_off:end_off], encrypted_off)

            if not self.peer_cid_set:
                self.peer_cid = header.source_cid
                self.peer_cid_set = True

            # handle payload
            is_ack_only = self._payload_received(epoch, plain_payload)

            # record packet as received
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

    def _payload_received(self, epoch, plain):
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
                stream = self.streams[epoch]
                stream.add_frame(pull_crypto_frame(buf))
                data = stream.pull_data()
                if data:
                    self.tls.handle_message(data, self.send_buffer)
            elif frame_type == QuicFrameType.NEW_CONNECTION_ID:
                is_ack_only = False
                pull_new_connection_id_frame(buf)
            else:
                logger.warning('unhandled frame type %d', frame_type)
                break
        return is_ack_only

    def _serialize_parameters(self):
        buf = Buffer(capacity=512)
        push_quic_transport_parameters(buf, self.quic_transport_parameters,
                                       is_client=self.is_client)
        return buf.data

    def _update_traffic_key(self, direction, epoch, secret):
        if self.secrets_log_file is not None:
            label_row = self.is_client == (direction == tls.Direction.DECRYPT)
            label = SECRETS_LABELS[label_row][epoch.value]
            self.secrets_log_file.write('%s %s %s\n' % (
                label, self.tls.client_random.hex(), secret.hex()))
            self.secrets_log_file.flush()

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
        self.send_ack[epoch] = False
        send_data = self.send_buffer[epoch].data
        self.send_buffer[epoch].seek(0)

        buf = Buffer(capacity=PACKET_MAX_SIZE)
        offset = 0

        while space.crypto.send.is_valid() and (send_ack or send_data):
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

            # ACK
            if send_ack:
                push_uint_var(buf, QuicFrameType.ACK)
                push_ack_frame(buf, send_ack, 0)
                send_ack = False

            if send_data:
                # CRYPTO
                chunk_size = min(len(send_data),
                                 PACKET_MAX_SIZE - buf.tell() - space.crypto.aead_tag_size - 4)
                push_uint_var(buf, QuicFrameType.CRYPTO)
                with push_crypto_frame(buf, offset):
                    chunk = send_data[:chunk_size]
                    tls.push_bytes(buf, chunk)
                    send_data = send_data[chunk_size:]
                    offset += chunk_size

                # PADDING
                if epoch == tls.Epoch.INITIAL and self.is_client:
                    tls.push_bytes(
                        buf,
                        bytes(PACKET_MAX_SIZE - space.crypto.aead_tag_size - buf.tell()))

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
            buf.seek(0)
