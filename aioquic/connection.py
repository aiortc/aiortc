import asyncio
import logging
import os
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, TextIO, Tuple, Union

from . import packet, tls
from .buffer import (
    pull_bytes,
    pull_uint16,
    pull_uint32,
    push_bytes,
    push_uint8,
    push_uint16,
)
from .crypto import CryptoError, CryptoPair
from .packet import (
    PACKET_FIXED_BIT,
    PACKET_NUMBER_SEND_SIZE,
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
    get_spin_bit,
    is_long_header,
    pull_quic_header,
    pull_quic_transport_parameters,
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
    if frame_type is None:
        push_uint_var(buf, QuicFrameType.APPLICATION_CLOSE)
        packet.push_application_close_frame(buf, error_code, reason_phrase)
    else:
        push_uint_var(buf, QuicFrameType.TRANSPORT_CLOSE)
        packet.push_transport_close_frame(buf, error_code, frame_type, reason_phrase)


def stream_is_client_initiated(stream_id: int) -> bool:
    """
    Returns True if the stream is client initiated.
    """
    return not (stream_id & 1)


def stream_is_unidirectional(stream_id: int) -> bool:
    """
    Returns True if the stream is unidirectional.
    """
    return bool(stream_id & 2)


class PacketSpace:
    def __init__(self) -> None:
        self.ack_queue = RangeSet()
        self.crypto = CryptoPair()


class QuicConnectionError(Exception):
    def __init__(self, error_code: int, frame_type: int, reason_phrase: str):
        self.error_code = error_code
        self.frame_type = frame_type
        self.reason_phrase = reason_phrase

    def __str__(self) -> str:
        return "Error: %d, reason: %s" % (self.error_code, self.reason_phrase)


class QuicConnectionState(Enum):
    FIRSTFLIGHT = 0
    CONNECTED = 1
    CLOSING = 2
    DRAINING = 3


def maybe_connection_error(
    error_code: int, frame_type: Optional[int], reason_phrase: str
) -> Optional[QuicConnectionError]:
    if error_code != QuicErrorCode.NO_ERROR:
        return QuicConnectionError(
            error_code=error_code, frame_type=frame_type, reason_phrase=reason_phrase
        )
    else:
        return None


class QuicConnection:
    """
    A QUIC connection.

    :param: is_client: `True` for a client, `False` for a server.
    :param: certificate: For a server, its certificate.
            See :func:`cryptography.x509.load_pem_x509_certificate`.
    :param: private_key: For a server, its private key.
            See :func:`cryptography.hazmat.primitives.serialization.load_pem_private_key`.
    """

    supported_versions = [QuicProtocolVersion.DRAFT_19, QuicProtocolVersion.DRAFT_20]

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

        self.__close: Optional[Dict] = None
        self.__connected = asyncio.Event()
        self.__epoch = tls.Epoch.INITIAL
        self._local_idle_timeout = 60000  # milliseconds
        self._local_max_data = 1048576
        self._local_max_data_used = 0
        self._local_max_stream_data_bidi_local = 1048576
        self._local_max_stream_data_bidi_remote = 1048576
        self._local_max_stream_data_uni = 1048576
        self._local_max_streams_bidi = 128
        self._local_max_streams_uni = 128
        self.__logger = logger
        self.__path_challenge: Optional[bytes] = None
        self.__peer_addr: Optional[Any] = None
        self._pending_flow_control: List[bytes] = []
        self._remote_idle_timeout = 0  # milliseconds
        self._remote_max_data = 0
        self._remote_max_stream_data_bidi_local = 0
        self._remote_max_stream_data_bidi_remote = 0
        self._remote_max_stream_data_uni = 0
        self._remote_max_streams_bidi = 0
        self._remote_max_streams_uni = 0
        self._spin_bit = False
        self._spin_highest_pn = 0
        self.__send_pending_task: Optional[asyncio.Handle] = None
        self.__state = QuicConnectionState.FIRSTFLIGHT
        self.__transport: Optional[asyncio.DatagramTransport] = None
        self.__version: Optional[int] = None

        # callbacks
        self.stream_created_cb: Callable[
            [asyncio.StreamReader, asyncio.StreamWriter], None
        ] = lambda r, w: None

        # frame handlers
        self.__frame_handlers = [
            self._handle_padding_frame,
            self._handle_padding_frame,
            self._handle_ack_frame,
            self._handle_ack_frame,
            self._handle_reset_stream_frame,
            self._handle_stop_sending_frame,
            self._handle_crypto_frame,
            self._handle_new_token_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_stream_frame,
            self._handle_max_data_frame,
            self._handle_max_stream_data_frame,
            self._handle_max_streams_bidi_frame,
            self._handle_max_streams_uni_frame,
            self._handle_data_blocked_frame,
            self._handle_stream_data_blocked_frame,
            self._handle_streams_blocked_frame,
            self._handle_streams_blocked_frame,
            self._handle_new_connection_id_frame,
            self._handle_retire_connection_id_frame,
            self._handle_path_challenge_frame,
            self._handle_path_response_frame,
            self._handle_connection_close_frame,
            self._handle_connection_close_frame,
        ]

    def close(
        self,
        error_code: int = QuicErrorCode.NO_ERROR,
        frame_type: Optional[int] = None,
        reason_phrase: str = "",
    ) -> None:
        """
        Close the connection.
        """
        self.__close = {
            "error_code": error_code,
            "frame_type": frame_type,
            "reason_phrase": reason_phrase,
        }
        if self.__state not in [
            QuicConnectionState.CLOSING,
            QuicConnectionState.DRAINING,
        ]:
            self._set_state(QuicConnectionState.CLOSING)
            self.connection_lost(
                maybe_connection_error(
                    error_code=error_code,
                    frame_type=frame_type,
                    reason_phrase=reason_phrase,
                )
            )
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

        if is_unidirectional:
            max_stream_data_local = 0
            max_stream_data_remote = self._remote_max_stream_data_uni
        else:
            max_stream_data_local = self._local_max_stream_data_bidi_local
            max_stream_data_remote = self._remote_max_stream_data_bidi_remote

        # create stream
        stream = self.streams[stream_id] = QuicStream(
            connection=self,
            stream_id=stream_id,
            max_stream_data_local=max_stream_data_local,
            max_stream_data_remote=max_stream_data_remote,
        )
        self.stream_created_cb(stream.reader, stream.writer)

        return stream.reader, stream.writer

    # asyncio.DatagramProtocol

    def connection_lost(self, exc: Exception) -> None:
        for stream in self.streams.values():
            stream.connection_lost(exc)

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """
        Inform the connection of the transport used to send data. This object
        must have a ``sendto`` method which accepts a datagram to send.

        Calling :meth:`connection_made` on a client starts the TLS handshake.
        """
        self.__transport = transport
        if self.is_client:
            self.__version = max(self.supported_versions)
            self._connect()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        """
        Handle an incoming datagram.
        """
        buf = Buffer(data=data)

        # stop handling packets when closing
        if self.__state in [QuicConnectionState.CLOSING, QuicConnectionState.DRAINING]:
            return

        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))

            # check destination CID matches
            if self.is_client and header.destination_cid != self.host_cid:
                return

            # check protocol version
            if self.is_client and header.version == QuicProtocolVersion.NEGOTIATION:
                # version negotiation
                versions = []
                while not buf.eof():
                    versions.append(pull_uint32(buf))
                common = set(self.supported_versions).intersection(versions)
                if not common:
                    self.__logger.error("Could not find a common protocol version")
                    return
                self.__version = QuicProtocolVersion(max(common))
                self.__logger.info("Retrying with %s", self.__version)
                self._connect()
                return
            elif (
                header.version is not None
                and header.version not in self.supported_versions
            ):
                # unsupported version
                return

            if self.is_client and header.packet_type == PACKET_TYPE_RETRY:
                # stateless retry
                if (
                    header.destination_cid == self.host_cid
                    and header.original_destination_cid == self.peer_cid
                ):
                    self.__logger.info("Performing stateless retry")
                    self.peer_cid = header.source_cid
                    self.peer_token = header.token
                    self._connect()
                return

            # server initialization
            if not self.is_client and self.__state == QuicConnectionState.FIRSTFLIGHT:
                assert (
                    header.packet_type == PACKET_TYPE_INITIAL
                ), "first packet must be INITIAL"
                self.__peer_addr = addr
                self.__version = QuicProtocolVersion(header.version)
                self._initialize(header.destination_cid)

            # decrypt packet
            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            pull_bytes(buf, header.rest_length)

            epoch = get_epoch(header.packet_type)
            space = self.spaces[epoch]
            if not space.crypto.recv.is_valid():
                return
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

            # update state
            if self.__state == QuicConnectionState.FIRSTFLIGHT:
                self._set_state(QuicConnectionState.CONNECTED)

            # update spin bit
            if (
                not is_long_header(plain_header[0])
                and packet_number > self._spin_highest_pn
            ):
                if self.is_client:
                    self._spin_bit = not get_spin_bit(plain_header[0])
                else:
                    self._spin_bit = get_spin_bit(plain_header[0])
                self._spin_highest_pn = packet_number

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

    def _assert_stream_can_receive(self, frame_type: int, stream_id: int) -> None:
        """
        Check the specified stream can receive data or raises a QuicConnectionError.
        """
        if not self._stream_can_receive(stream_id):
            raise QuicConnectionError(
                error_code=QuicErrorCode.STREAM_STATE_ERROR,
                frame_type=frame_type,
                reason_phrase="Stream is send-only",
            )

    def _assert_stream_can_send(self, frame_type: int, stream_id: int) -> None:
        """
        Check the specified stream can send data or raises a QuicConnectionError.
        """
        if not self._stream_can_send(stream_id):
            raise QuicConnectionError(
                error_code=QuicErrorCode.STREAM_STATE_ERROR,
                frame_type=frame_type,
                reason_phrase="Stream is receive-only",
            )

    def _connect(self) -> None:
        """
        Start the client handshake.
        """
        assert self.is_client

        self._initialize(self.peer_cid)

        self.tls.handle_message(b"", self.send_buffer)
        self._push_crypto_data()
        self._send_pending()

    def _get_or_create_stream(self, frame_type: int, stream_id: int) -> QuicStream:
        """
        Get or create a stream in response to a received frame.
        """
        stream = self.streams.get(stream_id, None)
        if stream is None:
            # check initiator
            if stream_is_client_initiated(stream_id) == self.is_client:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.STREAM_STATE_ERROR,
                    frame_type=frame_type,
                    reason_phrase="Wrong stream initiator",
                )

            # determine limits
            if stream_is_unidirectional(stream_id):
                max_stream_data_local = self._local_max_stream_data_uni
                max_stream_data_remote = 0
                max_streams = self._local_max_streams_uni
            else:
                max_stream_data_local = self._local_max_stream_data_bidi_remote
                max_stream_data_remote = self._remote_max_stream_data_bidi_local
                max_streams = self._local_max_streams_bidi

            # check max streams
            if stream_id // 4 >= max_streams:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.STREAM_LIMIT_ERROR,
                    frame_type=frame_type,
                    reason_phrase="Too many streams open",
                )

            # create stream
            stream = self.streams[stream_id] = QuicStream(
                connection=self,
                stream_id=stream_id,
                max_stream_data_local=max_stream_data_local,
                max_stream_data_remote=max_stream_data_remote,
            )
            self.stream_created_cb(stream.reader, stream.writer)
        return stream

    def _initialize(self, peer_cid: bytes) -> None:
        # TLS
        self.tls = tls.Context(is_client=self.is_client, logger=self.__logger)
        self.tls.alpn_protocols = self.alpn_protocols
        self.tls.certificate = self.certificate
        self.tls.certificate_private_key = self.private_key
        self.tls.handshake_extensions = [
            (
                tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                self._serialize_transport_parameters(),
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

        self.packet_number = 0

    def _handle_ack_frame(self, epoch: tls.Epoch, frame_type: int, buf: Buffer) -> None:
        """
        Handle an ACK frame.
        """
        packet.pull_ack_frame(buf)
        if frame_type == QuicFrameType.ACK_ECN:
            pull_uint_var(buf)
            pull_uint_var(buf)
            pull_uint_var(buf)

    def _handle_connection_close_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a CONNECTION_CLOSE frame.
        """
        if frame_type == QuicFrameType.TRANSPORT_CLOSE:
            error_code, frame_type, reason_phrase = packet.pull_transport_close_frame(
                buf
            )
        else:
            error_code, reason_phrase = packet.pull_application_close_frame(buf)
            frame_type = None
        self.__logger.info(
            "Connection close code 0x%X, reason %s", error_code, reason_phrase
        )
        self._set_state(QuicConnectionState.DRAINING)
        self.connection_lost(
            maybe_connection_error(
                error_code=error_code,
                frame_type=frame_type,
                reason_phrase=reason_phrase,
            )
        )

    def _handle_crypto_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a CRYPTO frame.
        """
        stream = self.streams[epoch]
        stream.add_frame(packet.pull_crypto_frame(buf))
        data = stream.pull_data()
        if data:
            # pass data to TLS layer
            try:
                self.tls.handle_message(data, self.send_buffer)
            except tls.Alert as exc:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.CRYPTO_ERROR + int(exc.description),
                    frame_type=QuicFrameType.CRYPTO,
                    reason_phrase=str(exc),
                )

            # update current epoch
            if self.tls.state in [
                tls.State.CLIENT_POST_HANDSHAKE,
                tls.State.SERVER_POST_HANDSHAKE,
            ]:
                if not self.__connected.is_set():
                    # parse transport parameters
                    for ext_type, ext_data in self.tls.received_extensions:
                        if ext_type == tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS:
                            self._parse_transport_parameters(ext_data)
                            break
                    self.__connected.set()
                self.__epoch = tls.Epoch.ONE_RTT
            else:
                self.__epoch = tls.Epoch.HANDSHAKE

    def _handle_data_blocked_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a DATA_BLOCKED frame.
        """
        pull_uint_var(buf)  # limit

    def _handle_max_data_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_DATA frame.

        This adjusts the total amount of we can send to the peer.
        """
        max_data = pull_uint_var(buf)
        if max_data > self._remote_max_data:
            self.__logger.info("Remote max_data raised to %d", max_data)
            self._remote_max_data = max_data

    def _handle_max_stream_data_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAM_DATA frame.

        This adjusts the amount of data we can send on a specific stream.
        """
        stream_id = pull_uint_var(buf)
        max_stream_data = pull_uint_var(buf)

        # check stream direction
        self._assert_stream_can_send(frame_type, stream_id)

        stream = self._get_or_create_stream(frame_type, stream_id)
        if max_stream_data > stream.max_stream_data_remote:
            self.__logger.info(
                "Stream %d remote max_stream_data raised to %d",
                stream_id,
                max_stream_data,
            )
            stream.max_stream_data_remote = max_stream_data

    def _handle_max_streams_bidi_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAMS_BIDI frame.

        This raises number of bidirectional streams we can initiate to the peer.
        """
        max_streams = pull_uint_var(buf)
        if max_streams > self._remote_max_streams_bidi:
            self.__logger.info("Remote max_streams_bidi raised to %d", max_streams)
            self._remote_max_streams_bidi = max_streams

    def _handle_max_streams_uni_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAMS_UNI frame.

        This raises number of unidirectional streams we can initiate to the peer.
        """
        max_streams = pull_uint_var(buf)
        if max_streams > self._remote_max_streams_uni:
            self.__logger.info("Remote max_streams_uni raised to %d", max_streams)
            self._remote_max_streams_uni = max_streams

    def _handle_new_connection_id_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a NEW_CONNECTION_ID frame.
        """
        packet.pull_new_connection_id_frame(buf)

    def _handle_new_token_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a NEW_TOKEN frame.
        """
        packet.pull_new_token_frame(buf)

    def _handle_padding_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PADDING or PING frame.
        """
        pass

    def _handle_path_challenge_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PATH_CHALLENGE frame.
        """
        data = pull_bytes(buf, 8)
        self._pending_flow_control.append(bytes([QuicFrameType.PATH_RESPONSE]) + data)

    def _handle_path_response_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PATH_RESPONSE frame.
        """
        data = pull_bytes(buf, 8)
        if data != self.__path_challenge:
            raise QuicConnectionError(
                error_code=QuicErrorCode.PROTOCOL_VIOLATION,
                frame_type=frame_type,
                reason_phrase="Response does not match challenge",
            )

    def _handle_reset_stream_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a RESET_STREAM frame.
        """
        stream_id = pull_uint_var(buf)
        pull_uint16(buf)  # application error code
        pull_uint16(buf)  # unused
        pull_uint_var(buf)  # final size

        # check stream direction
        self._assert_stream_can_receive(frame_type, stream_id)

        self._get_or_create_stream(frame_type, stream_id)

    def _handle_retire_connection_id_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a RETIRE_CONNECTION_ID frame.
        """
        pull_uint_var(buf)  # sequence number

    def _handle_stop_sending_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STOP_SENDING frame.
        """
        stream_id = pull_uint_var(buf)
        pull_uint16(buf)  # application error code

        # check stream direction
        self._assert_stream_can_send(frame_type, stream_id)

        self._get_or_create_stream(frame_type, stream_id)

    def _handle_stream_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAM frame.
        """
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

        # check stream direction
        self._assert_stream_can_receive(frame_type, stream_id)

        # check flow-control limits
        stream = self._get_or_create_stream(frame_type, stream_id)
        if offset + length > stream.max_stream_data_local:
            raise QuicConnectionError(
                error_code=QuicErrorCode.FLOW_CONTROL_ERROR,
                frame_type=frame_type,
                reason_phrase="Over stream data limit",
            )
        newly_received = max(0, offset + length - stream._recv_highest)
        if self._local_max_data_used + newly_received > self._local_max_data:
            raise QuicConnectionError(
                error_code=QuicErrorCode.FLOW_CONTROL_ERROR,
                frame_type=frame_type,
                reason_phrase="Over connection data limit",
            )

        stream.add_frame(frame)
        self._local_max_data_used += newly_received

    def _handle_stream_data_blocked_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAM_DATA_BLOCKED frame.
        """
        stream_id = pull_uint_var(buf)
        pull_uint_var(buf)  # limit

        # check stream direction
        self._assert_stream_can_receive(frame_type, stream_id)

        self._get_or_create_stream(frame_type, stream_id)

    def _handle_streams_blocked_frame(
        self, epoch: tls.Epoch, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAMS_BLOCKED frame.
        """
        pull_uint_var(buf)  # limit

    def _payload_received(self, epoch: tls.Epoch, plain: bytes) -> bool:
        buf = Buffer(data=plain)

        is_ack_only = True
        while not buf.eof():
            frame_type = pull_uint_var(buf)
            if frame_type not in [
                QuicFrameType.ACK,
                QuicFrameType.ACK_ECN,
                QuicFrameType.PADDING,
            ]:
                is_ack_only = False

            if frame_type < len(self.__frame_handlers):
                self.__frame_handlers[frame_type](epoch, frame_type, buf)
            else:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.PROTOCOL_VIOLATION,
                    frame_type=frame_type,
                    reason_phrase="Unexpected frame type",
                )

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

    def _send_path_challenge(self) -> None:
        self.__path_challenge = os.urandom(8)
        self._pending_flow_control.append(
            bytes([QuicFrameType.PATH_CHALLENGE]) + self.__path_challenge
        )
        self._send_pending()

    def _send_pending(self) -> None:
        for datagram in self._pending_datagrams():
            self.__transport.sendto(datagram, self.__peer_addr)
        self.__send_pending_task = None

    def _send_soon(self) -> None:
        if self.__send_pending_task is None:
            loop = asyncio.get_event_loop()
            self.__send_pending_task = loop.call_soon(self._send_pending)

    def _parse_transport_parameters(self, data: bytes) -> None:
        quic_transport_parameters = pull_quic_transport_parameters(Buffer(data=data))

        # store remote parameters
        if quic_transport_parameters.idle_timeout is not None:
            self._remote_idle_timeout = quic_transport_parameters.idle_timeout
        for param in [
            "max_data",
            "max_stream_data_bidi_local",
            "max_stream_data_bidi_remote",
            "max_stream_data_uni",
            "max_streams_bidi",
            "max_streams_uni",
        ]:
            value = getattr(quic_transport_parameters, "initial_" + param)
            if value is not None:
                setattr(self, "_remote_" + param, value)

    def _serialize_transport_parameters(self) -> bytes:
        quic_transport_parameters = QuicTransportParameters(
            idle_timeout=self._local_idle_timeout,
            initial_max_data=self._local_max_data,
            initial_max_stream_data_bidi_local=self._local_max_stream_data_bidi_local,
            initial_max_stream_data_bidi_remote=self._local_max_stream_data_bidi_remote,
            initial_max_stream_data_uni=self._local_max_stream_data_uni,
            initial_max_streams_bidi=self._local_max_streams_bidi,
            initial_max_streams_uni=self._local_max_streams_uni,
            ack_delay_exponent=10,
        )

        buf = Buffer(capacity=512)
        push_quic_transport_parameters(buf, quic_transport_parameters)
        return buf.data

    def _set_state(self, state: QuicConnectionState) -> None:
        self.__logger.info("%s -> %s", self.__state, state)
        self.__state = state

    def _stream_can_receive(self, stream_id: int) -> bool:
        return stream_is_client_initiated(
            stream_id
        ) != self.is_client or not stream_is_unidirectional(stream_id)

    def _stream_can_send(self, stream_id: int) -> bool:
        return stream_is_client_initiated(
            stream_id
        ) == self.is_client or not stream_is_unidirectional(stream_id)

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
            push_uint8(
                buf,
                PACKET_FIXED_BIT
                | (self._spin_bit << 5)
                | (space.crypto.key_phase << 2)
                | (PACKET_NUMBER_SEND_SIZE - 1),
            )
            push_bytes(buf, self.peer_cid)
            push_uint16(buf, self.packet_number)
            header_size = buf.tell()

            # ACK
            if self.send_ack[epoch] and space.ack_queue:
                push_uint_var(buf, QuicFrameType.ACK)
                packet.push_ack_frame(buf, space.ack_queue, 0)
                self.send_ack[epoch] = False

            # FLOW CONTROL
            for control_frame in self._pending_flow_control:
                push_bytes(buf, control_frame)
            self._pending_flow_control = []

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
                    version=self.__version,
                    packet_type=packet_type | (PACKET_NUMBER_SEND_SIZE - 1),
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
                buf.seek(header_size - PACKET_NUMBER_SEND_SIZE - 2)
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
