import asyncio
import logging
import os
from typing import Any, Dict, Iterator, List, Optional, TextIO, Tuple, Union

from . import packet, tls
from .buffer import pull_bytes, pull_uint32, push_bytes, push_uint8, push_uint16
from .crypto import CryptoError, CryptoPair
from .packet import (
    PACKET_FIXED_BIT,
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_RETRY,
    QuicErrorCode,
    QuicFrameType,
    QuicHeader,
    QuicProtocolVersion,
    QuicStreamFlag,
    QuicStreamFrame,
    QuicTransportParameters,
    pull_quic_header,
    pull_uint_var,
    push_quic_header,
    push_quic_transport_parameters,
    push_stream_frame,
    push_uint_var,
)
from .rangeset import RangeSet
from .stream import QuicStream
from .tls import Buffer

logger = logging.getLogger("quic")

PACKET_MAX_SIZE = 1280
SECRETS_LABELS = [
    [
        None,
        "QUIC_CLIENT_EARLY_TRAFFIC_SECRET",
        "QUIC_CLIENT_HANDSHAKE_TRAFFIC_SECRET",
        "QUIC_CLIENT_TRAFFIC_SECRET_0",
    ],
    [
        None,
        None,
        "QUIC_SERVER_HANDSHAKE_TRAFFIC_SECRET",
        "QUIC_SERVER_TRAFFIC_SECRET_0",
    ],
]
SEND_PN_SIZE = 2
STREAM_FLAGS = 0x07


def get_epoch(packet_type: int) -> tls.Epoch:
    if packet_type == PACKET_TYPE_INITIAL:
        return tls.Epoch.INITIAL
    elif packet_type == PACKET_TYPE_HANDSHAKE:
        return tls.Epoch.HANDSHAKE
    else:
        return tls.Epoch.ONE_RTT


def push_close(
    buf: Buffer, error_code: int, frame_type: Optional[int], reason_phrase: str
) -> None:
    reason_phrase_bytes = reason_phrase.encode("utf8")
    if frame_type is None:
        push_uint_var(buf, QuicFrameType.APPLICATION_CLOSE)
        packet.push_application_close_frame(buf, error_code, reason_phrase_bytes)
    else:
        push_uint_var(buf, QuicFrameType.TRANSPORT_CLOSE)
        packet.push_transport_close_frame(
            buf, error_code, frame_type, reason_phrase_bytes
        )


class PacketSpace:
    def __init__(self) -> None:
        self.ack_queue = RangeSet()
        self.crypto = CryptoPair()


class QuicConnectionError(Exception):
    def __init__(self, error_code: int, frame_type: int, reason_phrase: str):
        self.error_code = error_code
        self.frame_type = frame_type
        self.reason_phrase = reason_phrase

    def __str__(self):
        return "Error: %d, reason: %s" % (self.error_code, self.reason_phrase)


class QuicConnection:
    """
    A QUIC connection.

    :param: is_client: `True` for a client, `False` for a server.
    :param: certificate: For a server, its certificate.
            See :func:`cryptography.x509.load_pem_x509_certificate`.
    :param: private_key: For a server, its private key.
            See :func:`cryptography.hazmat.primitives.serialization.load_pem_private_key`.
    """

    def __init__(
        self,
        is_client: bool = True,
        certificate: Any = None,
        private_key: Any = None,
        secrets_log_file: TextIO = None,
        alpn_protocols: Optional[List[str]] = None,
        server_name: Optional[str] = None,
    ) -> None:
        if not is_client:
            assert certificate is not None, "SSL certificate is required"
            assert private_key is not None, "SSL private key is required"

        self.alpn_protocols = alpn_protocols
        self.certificate = certificate
        self.is_client = is_client
        self.host_cid = os.urandom(8)
        self.peer_cid = os.urandom(8)
        self.peer_cid_set = False
        self.peer_token = b""
        self.private_key = private_key
        self.secrets_log_file = secrets_log_file
        self.server_name = server_name
        self.streams: Dict[Union[tls.Epoch, int], QuicStream] = {}

        # protocol versions
        self.supported_versions = [
            QuicProtocolVersion.DRAFT_17,
            QuicProtocolVersion.DRAFT_18,
            QuicProtocolVersion.DRAFT_19,
            QuicProtocolVersion.DRAFT_20,
        ]
        self.version = QuicProtocolVersion.DRAFT_20

        self.__close: Optional[Dict] = None
        self.__connected = asyncio.Event()
        self.__epoch = tls.Epoch.INITIAL
        self.__initialized = False
        self.__local_max_streams_bidi = 100
        self.__local_max_streams_uni = 0
        self.__logger = logger
        self.__transport: Optional[asyncio.DatagramTransport] = None

    def close(
        self, error_code: int, frame_type: Optional[int] = None, reason_phrase: str = ""
    ) -> None:
        """
        Close the connection.
        """
        self.__close = {
            "error_code": error_code,
            "frame_type": frame_type,
            "reason_phrase": reason_phrase,
        }
        self._send_pending()

    async def connect(self) -> None:
        """
        Wait for the TLS handshake to complete.
        """
        await self.__connected.wait()

    def create_stream(
        self, is_unidirectional: bool = False
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """
        Create a QUIC stream and return a pair of (reader, writer) objects.

        The returned reader and writer objects are instances of :class:`asyncio.StreamReader`
        and :class:`asyncio.StreamWriter` classes.
        """
        stream_id = (int(is_unidirectional) << 1) | int(not self.is_client)
        while stream_id in self.streams:
            stream_id += 4
        stream = self._get_or_create_stream(stream_id)
        return stream.reader, stream.writer

    # asyncio.DatagramProtocol

    def connection_lost(self, exc: Exception) -> None:
        for stream in self.streams.values():
            if stream.reader:
                stream.reader.feed_eof()

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """
        Inform the connection of the transport used to send data. This object
        must have a ``sendto`` method which accepts a datagram to send.

        Calling :meth:`connection_made` on a client starts the TLS handshake.
        """
        self.__transport = transport
        if self.is_client:
            self._initialize(self.peer_cid)

            self.tls.handle_message(b"", self.send_buffer)
            self._push_crypto_data()
            self._send_pending()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        """
        Handle an incoming datagram.
        """
        buf = Buffer(data=data)

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))

            if self.is_client and header.version == QuicProtocolVersion.NEGOTIATION:
                # version negotiation
                versions = []
                while not buf.eof():
                    versions.append(pull_uint32(buf))
                common = set(self.supported_versions).intersection(versions)
                if not common:
                    self.__logger.error("Could not find a common protocol version")
                    return
                self.version = QuicProtocolVersion(max(common))
                self.__logger.info("Retrying with %s" % self.version)
                self.connection_made(self.__transport)
                return
            elif self.is_client and header.packet_type == PACKET_TYPE_RETRY:
                # stateless retry
                if (
                    header.destination_cid == self.host_cid
                    and header.original_destination_cid == self.peer_cid
                ):
                    self.__logger.info("Performing stateless retry")
                    self.peer_cid = header.source_cid
                    self.peer_token = header.token
                    self.connection_made(self.__transport)
                return

            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            pull_bytes(buf, header.rest_length)

            if not self.is_client and not self.__initialized:
                self._initialize(header.destination_cid)

            epoch = get_epoch(header.packet_type)
            space = self.spaces[epoch]
            try:
                plain_header, plain_payload, packet_number = space.crypto.decrypt_packet(
                    data[start_off:end_off], encrypted_off
                )
            except CryptoError as exc:
                self.__logger.warning(exc)
                return

            if not self.peer_cid_set:
                self.peer_cid = header.source_cid
                self.peer_cid_set = True

            # handle payload
            try:
                is_ack_only = self._payload_received(epoch, plain_payload)
            except QuicConnectionError as exc:
                self.__logger.warning(exc)
                self.close(
                    error_code=exc.error_code,
                    frame_type=exc.frame_type,
                    reason_phrase=exc.reason_phrase,
                )
                return

            # record packet as received
            space.ack_queue.add(packet_number)
            if not is_ack_only:
                self.send_ack[epoch] = True

        self._send_pending()

    def error_received(self, exc: OSError) -> None:
        self.__logger.warning(exc)

    # Private

    def _get_or_create_stream(self, stream_id: int) -> QuicStream:
        if stream_id not in self.streams:
            self.streams[stream_id] = QuicStream(connection=self, stream_id=stream_id)
        return self.streams[stream_id]

    def _initialize(self, peer_cid: bytes) -> None:
        # build transport parameters
        quic_transport_parameters = QuicTransportParameters(
            initial_max_data=16777216,
            initial_max_stream_data_bidi_local=1048576,
            initial_max_stream_data_bidi_remote=1048576,
            initial_max_stream_data_uni=1048576,
            initial_max_streams_bidi=self.__local_max_streams_bidi,
            initial_max_streams_uni=self.__local_max_streams_uni,
            ack_delay_exponent=10,
        )
        if self.version >= QuicProtocolVersion.DRAFT_19:
            quic_transport_parameters.idle_timeout = 600000
        else:
            quic_transport_parameters.idle_timeout = 600
            if self.is_client:
                quic_transport_parameters.initial_version = self.version
            else:
                quic_transport_parameters.negotiated_version = self.version
                quic_transport_parameters.supported_versions = self.supported_versions
                quic_transport_parameters.stateless_reset_token = bytes(16)

        # TLS
        self.tls = tls.Context(is_client=self.is_client, logger=self.__logger)
        self.tls.alpn_protocols = self.alpn_protocols
        self.tls.certificate = self.certificate
        self.tls.certificate_private_key = self.private_key
        self.tls.handshake_extensions = [
            (
                tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                self._serialize_parameters(quic_transport_parameters),
            )
        ]
        self.tls.server_name = self.server_name
        self.tls.update_traffic_key_cb = self._update_traffic_key

        # packet spaces
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
        self.streams[tls.Epoch.INITIAL] = QuicStream()
        self.streams[tls.Epoch.HANDSHAKE] = QuicStream()
        self.streams[tls.Epoch.ONE_RTT] = QuicStream()

        self.spaces[tls.Epoch.INITIAL].crypto.setup_initial(
            cid=peer_cid, is_client=self.is_client
        )

        self.__initialized = True
        self.packet_number = 0

    def _payload_received(self, epoch: tls.Epoch, plain: bytes) -> bool:
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
                    # pass data to TLS layer
                    try:
                        self.tls.handle_message(data, self.send_buffer)
                    except tls.Alert as exc:
                        raise QuicConnectionError(
                            error_code=QuicErrorCode.CRYPTO_ERROR
                            + int(exc.description),
                            frame_type=frame_type,
                            reason_phrase=str(exc),
                        )

                    # update current epoch
                    if self.tls.state in [
                        tls.State.CLIENT_POST_HANDSHAKE,
                        tls.State.SERVER_POST_HANDSHAKE,
                    ]:
                        if not self.__connected.is_set():
                            self.__connected.set()
                        self.__epoch = tls.Epoch.ONE_RTT
                    else:
                        self.__epoch = tls.Epoch.HANDSHAKE
            elif frame_type == QuicFrameType.NEW_TOKEN:
                packet.pull_new_token_frame(buf)
            elif (frame_type & ~STREAM_FLAGS) == QuicFrameType.STREAM_BASE:
                flags = frame_type & STREAM_FLAGS
                stream_id = pull_uint_var(buf)
                if flags & QuicStreamFlag.OFF:
                    offset = pull_uint_var(buf)
                else:
                    offset = 0
                if flags & QuicStreamFlag.LEN:
                    length = pull_uint_var(buf)
                else:
                    length = buf.capacity - buf.tell()
                frame = QuicStreamFrame(
                    offset=offset,
                    data=pull_bytes(buf, length),
                    fin=bool(flags & QuicStreamFlag.FIN),
                )
                stream = self._get_or_create_stream(stream_id)
                stream.add_frame(frame)
            elif frame_type == QuicFrameType.MAX_DATA:
                pull_uint_var(buf)
            elif frame_type in [
                QuicFrameType.MAX_STREAMS_BIDI,
                QuicFrameType.MAX_STREAMS_UNI,
            ]:
                pull_uint_var(buf)
            elif frame_type == QuicFrameType.NEW_CONNECTION_ID:
                packet.pull_new_connection_id_frame(buf)
            elif frame_type == QuicFrameType.TRANSPORT_CLOSE:
                error_code, frame_type, reason_phrase = packet.pull_transport_close_frame(
                    buf
                )
                self.__logger.info(
                    "Transport close code 0x%X, reason %s" % (error_code, reason_phrase)
                )
                self.connection_lost(None)
            elif frame_type == QuicFrameType.APPLICATION_CLOSE:
                error_code, reason_phrase = packet.pull_application_close_frame(buf)
                self.__logger.info(
                    "Application close code 0x%X, reason %s"
                    % (error_code, reason_phrase)
                )
                self.connection_lost(None)
            else:
                self.__logger.warning("unhandled frame type %d", frame_type)
                break

        self._push_crypto_data()

        return is_ack_only

    def _pending_datagrams(self) -> Iterator[bytes]:
        for epoch in [tls.Epoch.INITIAL, tls.Epoch.HANDSHAKE]:
            yield from self._write_handshake(epoch)

        yield from self._write_application()

    def _push_crypto_data(self) -> None:
        for epoch, buf in self.send_buffer.items():
            self.streams[epoch].write(buf.data)
            buf.seek(0)

    def _send_pending(self) -> None:
        for datagram in self._pending_datagrams():
            self.__transport.sendto(datagram)

    def _serialize_parameters(
        self, quic_transport_parameters: QuicTransportParameters
    ) -> bytes:
        buf = Buffer(capacity=512)
        if self.version >= QuicProtocolVersion.DRAFT_19:
            is_client = None
        else:
            is_client = self.is_client
        push_quic_transport_parameters(
            buf, quic_transport_parameters, is_client=is_client
        )
        return buf.data

    def _update_traffic_key(
        self, direction: tls.Direction, epoch: tls.Epoch, secret: bytes
    ) -> None:
        if self.secrets_log_file is not None:
            label_row = self.is_client == (direction == tls.Direction.DECRYPT)
            label = SECRETS_LABELS[label_row][epoch.value]
            self.secrets_log_file.write(
                "%s %s %s\n" % (label, self.tls.client_random.hex(), secret.hex())
            )
            self.secrets_log_file.flush()

        crypto = self.spaces[epoch].crypto
        if direction == tls.Direction.ENCRYPT:
            crypto.send.setup(self.tls.key_schedule.cipher_suite, secret)
        else:
            crypto.recv.setup(self.tls.key_schedule.cipher_suite, secret)

    def _write_application(self) -> Iterator[bytes]:
        epoch = tls.Epoch.ONE_RTT
        space = self.spaces[epoch]
        if not space.crypto.send.is_valid():
            return

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        while True:
            # write header
            push_uint8(buf, PACKET_FIXED_BIT | (SEND_PN_SIZE - 1))
            push_bytes(buf, self.peer_cid)
            push_uint16(buf, self.packet_number)
            header_size = buf.tell()

            # ACK
            if self.send_ack[epoch] and space.ack_queue:
                push_uint_var(buf, QuicFrameType.ACK)
                packet.push_ack_frame(buf, space.ack_queue, 0)
                self.send_ack[epoch] = False

            # CLOSE
            if self.__close and self.__epoch == epoch:
                push_close(buf, **self.__close)
                self.__close = None

            # STREAM
            for stream_id, stream in self.streams.items():
                if isinstance(stream_id, int) and stream.has_data_to_send():
                    frame = stream.get_frame(
                        PACKET_MAX_SIZE - buf.tell() - space.crypto.aead_tag_size - 6
                    )
                    flags = QuicStreamFlag.LEN
                    if frame.offset:
                        flags |= QuicStreamFlag.OFF
                    if frame.fin:
                        flags |= QuicStreamFlag.FIN
                    push_uint_var(buf, QuicFrameType.STREAM_BASE | flags)
                    with push_stream_frame(buf, 0, frame.offset):
                        push_bytes(buf, frame.data)

            packet_size = buf.tell()
            if packet_size > header_size:
                # encrypt
                data = buf.data
                yield space.crypto.encrypt_packet(
                    data[0:header_size], data[header_size:packet_size]
                )

                self.packet_number += 1
                buf.seek(0)
            else:
                break

    def _write_handshake(self, epoch: tls.Epoch) -> Iterator[bytes]:
        space = self.spaces[epoch]
        if not space.crypto.send.is_valid():
            return

        buf = Buffer(capacity=PACKET_MAX_SIZE)

        while True:
            if epoch == tls.Epoch.INITIAL:
                packet_type = PACKET_TYPE_INITIAL
            else:
                packet_type = PACKET_TYPE_HANDSHAKE

            # write header
            push_quic_header(
                buf,
                QuicHeader(
                    version=self.version,
                    packet_type=packet_type | (SEND_PN_SIZE - 1),
                    destination_cid=self.peer_cid,
                    source_cid=self.host_cid,
                    token=self.peer_token,
                ),
            )
            header_size = buf.tell()

            # ACK
            if self.send_ack[epoch] and space.ack_queue:
                push_uint_var(buf, QuicFrameType.ACK)
                packet.push_ack_frame(buf, space.ack_queue, 0)
                self.send_ack[epoch] = False

            # CLOSE
            if self.__close and self.__epoch == epoch:
                push_close(buf, **self.__close)
                self.__close = None

            stream = self.streams[epoch]
            if stream.has_data_to_send():
                # CRYPTO
                frame = stream.get_frame(
                    PACKET_MAX_SIZE - buf.tell() - space.crypto.aead_tag_size - 4
                )
                push_uint_var(buf, QuicFrameType.CRYPTO)
                with packet.push_crypto_frame(buf, frame.offset):
                    push_bytes(buf, frame.data)

                # PADDING
                if epoch == tls.Epoch.INITIAL and self.is_client:
                    push_bytes(
                        buf,
                        bytes(
                            PACKET_MAX_SIZE - space.crypto.aead_tag_size - buf.tell()
                        ),
                    )

            packet_size = buf.tell()
            if packet_size > header_size:
                # finalize length
                buf.seek(header_size - SEND_PN_SIZE - 2)
                length = packet_size - header_size + 2 + space.crypto.aead_tag_size
                push_uint16(buf, length | 0x4000)
                push_uint16(buf, self.packet_number)
                buf.seek(packet_size)

                # encrypt
                data = buf.data
                yield space.crypto.encrypt_packet(
                    data[0:header_size], data[header_size:packet_size]
                )

                self.packet_number += 1
                buf.seek(0)
            else:
                break
