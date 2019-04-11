import logging
import os

from . import packet, tls
from .crypto import CryptoPair
from .packet import (PACKET_FIXED_BIT, PACKET_TYPE_HANDSHAKE,
                     PACKET_TYPE_INITIAL, QuicFrameType, QuicHeader,
                     QuicProtocolVersion, QuicStreamFrame,
                     QuicTransportParameters, pull_quic_header, pull_uint_var,
                     push_quic_header, push_quic_transport_parameters,
                     push_stream_frame, push_uint_var)
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
STREAM_FLAGS = 0x07
STREAM_FLAG_FIN = 1
STREAM_FLAG_LEN = 2
STREAM_FLAG_OFF = 4


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
    def __init__(self, is_client=True, certificate=None, private_key=None, secrets_log_file=None,
                 server_name=None):
        if not is_client:
            assert certificate is not None, 'SSL certificate is required'
            assert private_key is not None, 'SSL private key is required'

        self.certificate = certificate
        self.is_client = is_client
        self.host_cid = os.urandom(8)
        self.peer_cid = os.urandom(8)
        self.peer_cid_set = False
        self.private_key = private_key
        self.secrets_log_file = secrets_log_file
        self.server_name = server_name

        # protocol versions
        self.version = QuicProtocolVersion.DRAFT_18
        self.supported_versions = [
            QuicProtocolVersion.DRAFT_17,
            QuicProtocolVersion.DRAFT_18,
        ]

        self.quic_transport_parameters = QuicTransportParameters(
            idle_timeout=600,
            initial_max_data=16777216,
            initial_max_stream_data_bidi_local=1048576,
            initial_max_stream_data_bidi_remote=1048576,
            initial_max_stream_data_uni=1048576,
            initial_max_streams_bidi=100,
            ack_delay_exponent=10,
        )

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
        """
        At startup the client initiates the crypto handshake.
        """
        if self.is_client:
            self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(cid=self.peer_cid,
                                                                is_client=self.is_client)
            self._init_tls()
            self.crypto_initialized = True

            self.tls.handle_message(b'', self.send_buffer)
            self._push_crypto_data()

    def create_stream(self, is_unidirectional=False):
        """
        Create a stream and return it.
        """
        stream_id = (int(is_unidirectional) << 1) | int(not self.is_client)
        while stream_id in self.streams:
            stream_id += 4
        self.streams[stream_id] = QuicStream(stream_id=stream_id)
        return self.streams[stream_id]

    def datagram_received(self, data: bytes):
        """
        Handle an incoming datagram.
        """
        buf = Buffer(data=data)

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))

            # version negotiation
            if self.is_client and header.packet_type is None:
                versions = []
                while not buf.eof():
                    versions.append(tls.pull_uint32(buf))
                common = set(self.supported_versions).intersection(versions)
                self.version = max(common)
                self.connection_made()
                return

            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            tls.pull_bytes(buf, header.rest_length)

            if not self.is_client and not self.crypto_initialized:
                self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(cid=header.destination_cid,
                                                                    is_client=self.is_client)
                self._init_tls()
                self.crypto_initialized = True

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

    def _init_tls(self):
        if self.version >= QuicProtocolVersion.DRAFT_19:
            self.quic_transport_parameters.idle_timeout = 600000
        else:
            self.quic_transport_parameters.idle_timeout = 600
            if self.is_client:
                self.quic_transport_parameters.initial_version = self.version
            else:
                self.quic_transport_parameters.negotiated_version = self.version
                self.quic_transport_parameters.supported_versions = self.supported_versions
                self.quic_transport_parameters.stateless_reset_token = bytes(16)

        self.tls = tls.Context(is_client=self.is_client)
        self.tls.certificate = self.certificate
        self.tls.certificate_private_key = self.private_key
        self.tls.handshake_extensions = [
            (tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS, self._serialize_parameters()),
        ]
        self.tls.server_name = self.server_name
        self.tls.update_traffic_key_cb = self._update_traffic_key

    def _payload_received(self, epoch, plain):
        buf = Buffer(data=plain)

        is_ack_only = True
        while not buf.eof():
            frame_type = pull_uint_var(buf)
            if frame_type != QuicFrameType.ACK:
                is_ack_only = False

            if frame_type in [QuicFrameType.PADDING, QuicFrameType.PING]:
                pass
            elif frame_type == QuicFrameType.ACK:
                packet.pull_ack_frame(buf)
            elif frame_type == QuicFrameType.CRYPTO:
                stream = self.streams[epoch]
                stream.add_frame(packet.pull_crypto_frame(buf))
                data = stream.pull_data()
                if data:
                    self.tls.handle_message(data, self.send_buffer)
            elif frame_type == QuicFrameType.NEW_TOKEN:
                packet.pull_new_token_frame(buf)
            elif (frame_type & ~STREAM_FLAGS) == QuicFrameType.STREAM_BASE:
                flags = frame_type & STREAM_FLAGS
                stream_id = pull_uint_var(buf)
                if flags & STREAM_FLAG_OFF:
                    offset = pull_uint_var(buf)
                else:
                    offset = 0
                if flags & STREAM_FLAG_LEN:
                    length = pull_uint_var(buf)
                else:
                    length = buf.capacity - buf.tell()
                stream = self.streams[stream_id]
                stream.add_frame(QuicStreamFrame(offset=offset, data=tls.pull_bytes(buf, length)))
            elif frame_type == QuicFrameType.MAX_DATA:
                pull_uint_var(buf)
            elif frame_type in [QuicFrameType.MAX_STREAMS_BIDI, QuicFrameType.MAX_STREAMS_UNI]:
                pull_uint_var(buf)
            elif frame_type == QuicFrameType.NEW_CONNECTION_ID:
                packet.pull_new_connection_id_frame(buf)
            else:
                logger.warning('unhandled frame type %d', frame_type)
                break

        self._push_crypto_data()

        return is_ack_only

    def _push_crypto_data(self):
        for epoch, buf in self.send_buffer.items():
            self.streams[epoch].push_data(buf.data)
            buf.seek(0)

    def _serialize_parameters(self):
        buf = Buffer(capacity=512)
        if self.version >= QuicProtocolVersion.DRAFT_19:
            is_client = None
        else:
            is_client = self.is_client
        push_quic_transport_parameters(buf, self.quic_transport_parameters,
                                       is_client=is_client)
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
            crypto.send.setup(self.tls.key_schedule.cipher_suite, secret)
        else:
            crypto.recv.setup(self.tls.key_schedule.cipher_suite, secret)

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
            packet.push_ack_frame(buf, send_ack, 0)
            self.send_ack[epoch] = False

        # STREAM
        for stream_id, stream in self.streams.items():
            if isinstance(stream_id, int) and stream.has_data_to_send():
                frame = stream.get_frame(
                    PACKET_MAX_SIZE - buf.tell() - space.crypto.aead_tag_size - 6)
                push_uint_var(buf, QuicFrameType.STREAM_BASE + 0x07)
                with push_stream_frame(buf, 0, frame.offset):
                    tls.push_bytes(buf, frame.data)

        # encrypt
        packet_size = buf.tell()
        data = buf.data
        yield space.crypto.encrypt_packet(data[0:header_size], data[header_size:packet_size])

        self.packet_number += 1

    def _write_handshake(self, epoch):
        space = self.spaces[epoch]
        stream = self.streams[epoch]
        send_ack = space.ack_queue if self.send_ack[epoch] else False
        self.send_ack[epoch] = False

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        while space.crypto.send.is_valid() and (send_ack or stream.has_data_to_send()):
            if epoch == tls.Epoch.INITIAL:
                packet_type = PACKET_TYPE_INITIAL
            else:
                packet_type = PACKET_TYPE_HANDSHAKE

            # write header
            push_quic_header(buf, QuicHeader(
                version=self.version,
                packet_type=packet_type | (SEND_PN_SIZE - 1),
                destination_cid=self.peer_cid,
                source_cid=self.host_cid,
            ))
            header_size = buf.tell()

            # ACK
            if send_ack:
                push_uint_var(buf, QuicFrameType.ACK)
                packet.push_ack_frame(buf, send_ack, 0)
                send_ack = False

            if stream.has_data_to_send():
                # CRYPTO
                frame = stream.get_frame(
                    PACKET_MAX_SIZE - buf.tell() - space.crypto.aead_tag_size - 4)
                push_uint_var(buf, QuicFrameType.CRYPTO)
                with packet.push_crypto_frame(buf, frame.offset):
                    tls.push_bytes(buf, frame.data)

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
