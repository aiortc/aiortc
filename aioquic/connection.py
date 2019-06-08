from __future__ import annotations

import asyncio
import binascii
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Text,
    TextIO,
    Tuple,
    Union,
    cast,
)

from . import tls
from .buffer import (
    Buffer,
    BufferReadError,
    pull_bytes,
    pull_uint16,
    pull_uint32,
    pull_uint_var,
    push_bytes,
    push_uint16,
    push_uint_var,
    size_uint_var,
)
from .crypto import CryptoError, CryptoPair
from .packet import (
    NON_ACK_ELICITING_FRAME_TYPES,
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_ONE_RTT,
    PACKET_TYPE_RETRY,
    PACKET_TYPE_ZERO_RTT,
    PROBING_FRAME_TYPES,
    QuicErrorCode,
    QuicFrameType,
    QuicProtocolVersion,
    QuicStreamFrame,
    QuicTransportParameters,
    get_spin_bit,
    pull_ack_frame,
    pull_application_close_frame,
    pull_crypto_frame,
    pull_new_connection_id_frame,
    pull_new_token_frame,
    pull_quic_header,
    pull_quic_transport_parameters,
    pull_transport_close_frame,
    push_ack_frame,
    push_new_connection_id_frame,
    push_quic_transport_parameters,
)
from .packet_builder import QuicDeliveryState, QuicPacketBuilder
from .recovery import QuicPacketRecovery, QuicPacketSpace
from .stream import QuicStream

logger = logging.getLogger("quic")

EPOCH_SHORTCUTS = {
    "I": tls.Epoch.INITIAL,
    "Z": tls.Epoch.ZERO_RTT,
    "H": tls.Epoch.HANDSHAKE,
    "O": tls.Epoch.ONE_RTT,
}
MAX_DATA_WINDOW = 1048576
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

NetworkAddress = Any


def EPOCHS(shortcut: str) -> FrozenSet[tls.Epoch]:
    return frozenset(EPOCH_SHORTCUTS[i] for i in shortcut)


def dump_cid(cid: bytes) -> str:
    return binascii.hexlify(cid).decode("ascii")


def get_epoch(packet_type: int) -> tls.Epoch:
    if packet_type == PACKET_TYPE_INITIAL:
        return tls.Epoch.INITIAL
    elif packet_type == PACKET_TYPE_ZERO_RTT:
        return tls.Epoch.ZERO_RTT
    elif packet_type == PACKET_TYPE_HANDSHAKE:
        return tls.Epoch.HANDSHAKE
    else:
        return tls.Epoch.ONE_RTT


def frame_type_name(frame_type: int) -> str:
    if (
        frame_type >= QuicFrameType.STREAM_BASE
        and frame_type < QuicFrameType.STREAM_BASE + 16
    ):
        return "STREAM"
    else:
        return QuicFrameType(frame_type).name


def write_close_frame(
    builder: QuicPacketBuilder,
    error_code: int,
    frame_type: Optional[int],
    reason_phrase: str,
) -> None:
    buf = builder.buffer

    reason_bytes = reason_phrase.encode("utf8")

    if frame_type is None:
        builder.start_frame(QuicFrameType.APPLICATION_CLOSE)
        push_uint16(buf, error_code)
        push_uint_var(buf, len(reason_bytes))
        push_bytes(buf, reason_bytes)
    else:
        builder.start_frame(QuicFrameType.TRANSPORT_CLOSE)
        push_uint16(buf, error_code)
        push_uint_var(buf, frame_type)
        push_uint_var(buf, len(reason_bytes))
        push_bytes(buf, reason_bytes)


def write_crypto_frame(
    builder: QuicPacketBuilder, space: QuicPacketSpace, stream: QuicStream
) -> None:
    frame_overhead = 3 + size_uint_var(stream.next_send_offset)
    frame = stream.get_frame(builder.remaining_space - frame_overhead)
    if frame is not None:
        builder.start_frame(
            QuicFrameType.CRYPTO,
            stream.on_data_delivery,
            (frame.offset, frame.offset + len(frame.data)),
        )
        push_uint_var(builder.buffer, frame.offset)
        push_uint16(builder.buffer, len(frame.data) | 0x4000)
        push_bytes(builder.buffer, frame.data)


def write_stream_frame(
    builder: QuicPacketBuilder,
    space: QuicPacketSpace,
    stream: QuicStream,
    max_offset: int,
) -> int:
    buf = builder.buffer

    # the frame data size is constrained by our peer's MAX_DATA and
    # the space available in the current packet
    frame_overhead = (
        3
        + size_uint_var(stream.stream_id)
        + (size_uint_var(stream.next_send_offset) if stream.next_send_offset else 0)
    )
    previous_send_highest = stream._send_highest
    frame = stream.get_frame(builder.remaining_space - frame_overhead, max_offset)

    if frame is not None:
        frame_type = QuicFrameType.STREAM_BASE | 2  # length
        if frame.offset:
            frame_type |= 4
        if frame.fin:
            frame_type |= 1
        builder.start_frame(
            frame_type,
            stream.on_data_delivery,
            (frame.offset, frame.offset + len(frame.data)),
        )
        push_uint_var(buf, stream.stream_id)
        if frame.offset:
            push_uint_var(buf, frame.offset)
        push_uint16(buf, len(frame.data) | 0x4000)
        push_bytes(buf, frame.data)
        return stream._send_highest - previous_send_highest
    else:
        return 0


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


class QuicConnectionError(Exception):
    def __init__(self, error_code: int, frame_type: int, reason_phrase: str):
        self.error_code = error_code
        self.frame_type = frame_type
        self.reason_phrase = reason_phrase

    def __str__(self) -> str:
        s = "Error: %d, reason: %s" % (self.error_code, self.reason_phrase)
        if self.frame_type is not None:
            s += ", frame_type: %s" % self.frame_type
        return s


class QuicConnectionAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: Any) -> Tuple[str, Any]:
        return "[%s] %s" % (self.extra["host_cid"], msg), kwargs


@dataclass
class QuicConnectionId:
    cid: bytes
    sequence_number: int
    stateless_reset_token: bytes = b""
    was_sent: bool = False


class QuicConnectionState(Enum):
    FIRSTFLIGHT = 0
    CONNECTED = 1
    CLOSING = 2
    DRAINING = 3


@dataclass
class QuicNetworkPath:
    addr: NetworkAddress
    bytes_received: int = 0
    bytes_sent: int = 0
    is_validated: bool = False
    local_challenge: Optional[bytes] = None
    remote_challenge: Optional[bytes] = None

    def can_send(self, size: int) -> bool:
        return self.is_validated or (self.bytes_sent + size) <= 3 * self.bytes_received


@dataclass
class QuicReceiveContext:
    epoch: tls.Epoch
    host_cid: bytes
    network_path: QuicNetworkPath
    time: float

    def __str__(self) -> str:
        return self.epoch.name


def maybe_connection_error(
    error_code: int, frame_type: Optional[int], reason_phrase: str
) -> Optional[QuicConnectionError]:
    if error_code != QuicErrorCode.NO_ERROR:
        return QuicConnectionError(
            error_code=error_code, frame_type=frame_type, reason_phrase=reason_phrase
        )
    else:
        return None


QuicConnectionIdHandler = Callable[[bytes], None]
QuicStreamHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], None]


class QuicConnection(asyncio.DatagramProtocol):
    """
    A QUIC connection.
    """

    supported_versions = [QuicProtocolVersion.DRAFT_19, QuicProtocolVersion.DRAFT_20]

    def __init__(
        self,
        *,
        is_client: bool = True,
        certificate: Any = None,
        private_key: Any = None,
        alpn_protocols: Optional[List[str]] = None,
        original_connection_id: Optional[bytes] = None,
        secrets_log_file: TextIO = None,
        server_name: Optional[str] = None,
        session_ticket: Optional[tls.SessionTicket] = None,
        session_ticket_fetcher: Optional[tls.SessionTicketFetcher] = None,
        session_ticket_handler: Optional[tls.SessionTicketHandler] = None,
        supported_versions: Optional[List[QuicProtocolVersion]] = None,
        stream_handler: Optional[QuicStreamHandler] = None,
    ) -> None:
        if is_client:
            assert (
                original_connection_id is None
            ), "Cannot set original_connection_id for a client"
        else:
            assert certificate is not None, "SSL certificate is required for a server"
            assert private_key is not None, "SSL private key is required for a server"

        self.alpn_protocols = alpn_protocols
        self.certificate = certificate
        self.is_client = is_client
        self.peer_cid = os.urandom(8)
        self._peer_cid_seq: Optional[int] = None
        self._peer_cid_available: List[QuicConnectionId] = []
        self.peer_token = b""
        self.private_key = private_key
        self.secrets_log_file = secrets_log_file
        self.server_name = server_name
        self.streams: Dict[Union[tls.Epoch, int], QuicStream] = {}
        if supported_versions is not None:
            self.supported_versions = supported_versions

        # counters for debugging
        self._stateless_retry_count = 0
        self._version_negotiation_count = 0

        self._loop = asyncio.get_event_loop()
        self.__close: Optional[Dict] = None
        self._connect_called = False
        self._connected = asyncio.Event()
        self._handshake_complete = False
        self._handshake_confirmed = False
        self._host_cids = [
            QuicConnectionId(
                cid=os.urandom(8),
                sequence_number=0,
                stateless_reset_token=os.urandom(16),
                was_sent=True,
            )
        ]
        self.host_cid = self._host_cids[0].cid
        self._host_cid_seq = 1
        self._idle_timeout_at: Optional[float] = None
        self._local_idle_timeout = 60.0  # seconds
        self._local_max_data = MAX_DATA_WINDOW
        self._local_max_data_sent = MAX_DATA_WINDOW
        self._local_max_data_used = 0
        self._local_max_stream_data_bidi_local = MAX_DATA_WINDOW
        self._local_max_stream_data_bidi_remote = MAX_DATA_WINDOW
        self._local_max_stream_data_uni = MAX_DATA_WINDOW
        self._local_max_streams_bidi = 128
        self._local_max_streams_uni = 128
        self._logger = QuicConnectionAdapter(
            logger, {"host_cid": dump_cid(self.host_cid)}
        )
        self._loss = QuicPacketRecovery(
            logger=self._logger, send_probe=self._send_probe
        )
        self._loss_detection_at: Optional[float] = None
        self._network_paths: List[QuicNetworkPath] = []
        self._original_connection_id = original_connection_id
        self._packet_number = 0
        self._parameters_available = asyncio.Event()
        self._parameters_received = False
        self._ping_waiter: Optional[asyncio.Future[None]] = None
        self._remote_idle_timeout = 0.0  # seconds
        self._remote_max_data = 0
        self._remote_max_data_used = 0
        self._remote_max_stream_data_bidi_local = 0
        self._remote_max_stream_data_bidi_remote = 0
        self._remote_max_stream_data_uni = 0
        self._remote_max_streams_bidi = 0
        self._remote_max_streams_uni = 0
        self._session_ticket = session_ticket
        self._spin_bit = False
        self._spin_bit_peer = False
        self._spin_highest_pn = 0
        self.__send_pending_task: Optional[asyncio.Handle] = None
        self.__state = QuicConnectionState.FIRSTFLIGHT
        self._timer: Optional[asyncio.TimerHandle] = None
        self._timer_at: Optional[float] = None
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._version: Optional[int] = None

        # things to send
        self._ping_pending = False
        self._probe_pending = False
        self._retire_connection_ids: List[int] = []

        # callbacks
        self._connection_id_issued_handler: QuicConnectionIdHandler = lambda c: None
        self._connection_id_retired_handler: QuicConnectionIdHandler = lambda c: None
        self._session_ticket_fetcher = session_ticket_fetcher
        self._session_ticket_handler = session_ticket_handler

        if stream_handler is not None:
            self._stream_handler = stream_handler
        else:
            self._stream_handler = lambda r, w: None

        # frame handlers
        self.__frame_handlers = [
            (self._handle_padding_frame, EPOCHS("IZHO")),
            (self._handle_padding_frame, EPOCHS("ZO")),
            (self._handle_ack_frame, EPOCHS("IHO")),
            (self._handle_ack_frame, EPOCHS("IHO")),
            (self._handle_reset_stream_frame, EPOCHS("ZO")),
            (self._handle_stop_sending_frame, EPOCHS("ZO")),
            (self._handle_crypto_frame, EPOCHS("IHO")),
            (self._handle_new_token_frame, EPOCHS("O")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_stream_frame, EPOCHS("ZO")),
            (self._handle_max_data_frame, EPOCHS("ZO")),
            (self._handle_max_stream_data_frame, EPOCHS("ZO")),
            (self._handle_max_streams_bidi_frame, EPOCHS("ZO")),
            (self._handle_max_streams_uni_frame, EPOCHS("ZO")),
            (self._handle_data_blocked_frame, EPOCHS("ZO")),
            (self._handle_stream_data_blocked_frame, EPOCHS("ZO")),
            (self._handle_streams_blocked_frame, EPOCHS("ZO")),
            (self._handle_streams_blocked_frame, EPOCHS("ZO")),
            (self._handle_new_connection_id_frame, EPOCHS("ZO")),
            (self._handle_retire_connection_id_frame, EPOCHS("O")),
            (self._handle_path_challenge_frame, EPOCHS("ZO")),
            (self._handle_path_response_frame, EPOCHS("O")),
            (self._handle_connection_close_frame, EPOCHS("IZHO")),
            (self._handle_connection_close_frame, EPOCHS("ZO")),
        ]

    @property
    def alpn_protocol(self) -> Optional[str]:
        """
        The protocol which was negotiated via ALPN.
        """
        return self.tls.alpn_negotiated

    def close(
        self,
        error_code: int = QuicErrorCode.NO_ERROR,
        frame_type: Optional[int] = None,
        reason_phrase: str = "",
    ) -> None:
        """
        Close the connection.
        """
        if self.__state not in [
            QuicConnectionState.CLOSING,
            QuicConnectionState.DRAINING,
        ]:
            self.__close = {
                "error_code": error_code,
                "frame_type": frame_type,
                "reason_phrase": reason_phrase,
            }
            self._set_state(QuicConnectionState.CLOSING)
            self.connection_lost(
                maybe_connection_error(
                    error_code=error_code,
                    frame_type=frame_type,
                    reason_phrase=reason_phrase,
                )
            )
            self._send_pending()
            for epoch in self.spaces.keys():
                self._discard_epoch(epoch)

    def connect(
        self, addr: NetworkAddress, protocol_version: Optional[int] = None
    ) -> None:
        """
        Initiate the TLS handshake.

        This method can only be called for clients and a single time.
        """
        assert (
            self.is_client and not self._connect_called
        ), "connect() can only be called for clients and a single time"
        self._connect_called = True

        self._network_paths = [QuicNetworkPath(addr, is_validated=True)]
        if protocol_version is not None:
            self._version = protocol_version
        else:
            self._version = max(self.supported_versions)
        self._connect()

    async def create_stream(
        self, is_unidirectional: bool = False
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """
        Create a QUIC stream and return a pair of (reader, writer) objects.

        The returned reader and writer objects are instances of :class:`asyncio.StreamReader`
        and :class:`asyncio.StreamWriter` classes.
        """
        await self._parameters_available.wait()

        stream_id = (int(is_unidirectional) << 1) | int(not self.is_client)
        while stream_id in self.streams:
            stream_id += 4

        # determine limits
        if is_unidirectional:
            max_stream_data_local = 0
            max_stream_data_remote = self._remote_max_stream_data_uni
            max_streams = self._remote_max_streams_uni
        else:
            max_stream_data_local = self._local_max_stream_data_bidi_local
            max_stream_data_remote = self._remote_max_stream_data_bidi_remote
            max_streams = self._remote_max_streams_bidi

        # check max streams
        if stream_id // 4 >= max_streams:
            raise ValueError("Too many streams open")

        # create stream
        stream = self.streams[stream_id] = QuicStream(
            connection=self,
            stream_id=stream_id,
            max_stream_data_local=max_stream_data_local,
            max_stream_data_remote=max_stream_data_remote,
        )

        return stream.reader, stream.writer

    async def ping(self) -> None:
        """
        Pings the remote host and waits for the response.
        """
        assert self._ping_waiter is None, "already await a ping"
        self._ping_pending = True
        self._ping_waiter = self._loop.create_future()
        self._send_soon()
        await asyncio.shield(self._ping_waiter)

    def request_key_update(self) -> None:
        """
        Request an update of the encryption keys.
        """
        assert self._handshake_complete, "cannot change key before handshake completes"
        self.cryptos[tls.Epoch.ONE_RTT].update_key()

    async def wait_connected(self) -> None:
        """
        Wait for the TLS handshake to complete.
        """
        await self._connected.wait()

    # asyncio.DatagramProtocol

    def connection_lost(self, exc: Exception) -> None:
        for stream in self.streams.values():
            stream.connection_lost(exc)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = cast(asyncio.DatagramTransport, transport)

    def datagram_received(self, data: Union[bytes, Text], addr: NetworkAddress) -> None:
        """
        Handle an incoming datagram.
        """
        # stop handling packets when closing
        if self.__state in [QuicConnectionState.CLOSING, QuicConnectionState.DRAINING]:
            return

        data = cast(bytes, data)
        buf = Buffer(data=data)
        now = self._loop.time()
        while not buf.eof():
            start_off = buf.tell()
            header = pull_quic_header(buf, host_cid_length=len(self.host_cid))

            # check destination CID matches
            destination_cid_seq: Optional[int] = None
            for connection_id in self._host_cids:
                if header.destination_cid == connection_id.cid:
                    destination_cid_seq = connection_id.sequence_number
                    break
            if self.is_client and destination_cid_seq is None:
                return

            # check protocol version
            if self.is_client and header.version == QuicProtocolVersion.NEGOTIATION:
                # version negotiation
                versions = []
                while not buf.eof():
                    versions.append(pull_uint32(buf))
                common = set(self.supported_versions).intersection(versions)
                if not common:
                    self._logger.error("Could not find a common protocol version")
                    return
                self._version = QuicProtocolVersion(max(common))
                self._version_negotiation_count += 1
                self._logger.info("Retrying with %s", self._version)
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
                    and not self._stateless_retry_count
                ):
                    self._original_connection_id = self.peer_cid
                    self.peer_cid = header.source_cid
                    self.peer_token = header.token
                    self._stateless_retry_count += 1
                    self._logger.info("Performing stateless retry")
                    self._connect()
                return

            network_path = self._find_network_path(addr)

            # server initialization
            if not self.is_client and self.__state == QuicConnectionState.FIRSTFLIGHT:
                assert (
                    header.packet_type == PACKET_TYPE_INITIAL
                ), "first packet must be INITIAL"
                self._network_paths = [network_path]
                self._version = QuicProtocolVersion(header.version)
                self._initialize(header.destination_cid)

            # determine crypto and packet space
            epoch = get_epoch(header.packet_type)
            crypto = self.cryptos[epoch]
            if epoch == tls.Epoch.ZERO_RTT:
                space = self.spaces[tls.Epoch.ONE_RTT]
            else:
                space = self.spaces[epoch]

            # decrypt packet
            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            pull_bytes(buf, header.rest_length)

            try:
                plain_header, plain_payload, packet_number = crypto.decrypt_packet(
                    data[start_off:end_off], encrypted_off, space.expected_packet_number
                )
            except CryptoError as exc:
                self._logger.debug(exc)
                continue
            if packet_number > space.expected_packet_number:
                space.expected_packet_number = packet_number + 1

            # discard initial keys and packet space
            if not self.is_client and epoch == tls.Epoch.HANDSHAKE:
                self._discard_epoch(tls.Epoch.INITIAL)

            # update state
            if self._peer_cid_seq is None:
                self.peer_cid = header.source_cid
                self._peer_cid_seq = 0

            if self.__state == QuicConnectionState.FIRSTFLIGHT:
                self._set_state(QuicConnectionState.CONNECTED)

            # update spin bit
            if not header.is_long_header and packet_number > self._spin_highest_pn:
                self._spin_bit_peer = get_spin_bit(plain_header[0])
                if self.is_client:
                    self._spin_bit = not self._spin_bit_peer
                else:
                    self._spin_bit = self._spin_bit_peer
                self._spin_highest_pn = packet_number

            # handle payload
            context = QuicReceiveContext(
                epoch=epoch,
                host_cid=header.destination_cid,
                network_path=network_path,
                time=now,
            )
            try:
                is_ack_eliciting, is_probing = self._payload_received(
                    context, plain_payload
                )
            except QuicConnectionError as exc:
                self._logger.warning(exc)
                self.close(
                    error_code=exc.error_code,
                    frame_type=exc.frame_type,
                    reason_phrase=exc.reason_phrase,
                )
                return
            self._idle_timeout_at = now + self._local_idle_timeout

            # handle migration
            if (
                not self.is_client
                and context.host_cid != self.host_cid
                and epoch == tls.Epoch.ONE_RTT
            ):
                self._logger.info(
                    "Peer migrating to %s (%d)",
                    dump_cid(context.host_cid),
                    destination_cid_seq,
                )
                self.host_cid = context.host_cid
                self._consume_connection_id()

            # update network path
            if not network_path.is_validated and epoch == tls.Epoch.HANDSHAKE:
                self._logger.info(
                    "Network path %s validated by handshake", network_path.addr
                )
                network_path.is_validated = True
            network_path.bytes_received += buf.tell() - start_off
            if network_path not in self._network_paths:
                self._network_paths.append(network_path)
            idx = self._network_paths.index(network_path)
            if idx and not is_probing:
                self._logger.info("Network path %s promoted", network_path.addr)
                self._network_paths.pop(idx)
                self._network_paths.insert(0, network_path)

            # record packet as received
            space.ack_queue.add(packet_number)
            if len(space.ack_queue) > 63:
                space.ack_queue.shift()
            if is_ack_eliciting:
                space.ack_required = True

        self._send_pending()

    def error_received(self, exc: Exception) -> None:
        self._logger.warning(exc)

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

        self._idle_timeout_at = self._loop.time() + self._local_idle_timeout
        self._initialize(self.peer_cid)

        self.tls.handle_message(b"", self.send_buffer)
        self._push_crypto_data()
        self._send_pending()

    def _consume_connection_id(self) -> None:
        """
        Switch to the next available connection ID and retire
        the previous one.
        """
        if self._peer_cid_available:
            # retire previous CID
            self._retire_connection_ids.append(self._peer_cid_seq)

            # assign new CID
            connection_id = self._peer_cid_available.pop(0)
            self._peer_cid_seq = connection_id.sequence_number
            self.peer_cid = connection_id.cid
            self._logger.info(
                "Migrating to %s (%d)", dump_cid(self.peer_cid), self._peer_cid_seq
            )

    def _discard_epoch(self, epoch: tls.Epoch) -> None:
        self._logger.debug("Discarding epoch %s", epoch)
        self.cryptos[epoch].teardown()
        self._loss.discard_space(self.spaces[epoch])

    def _find_network_path(self, addr: NetworkAddress) -> QuicNetworkPath:
        # check existing network paths
        for idx, network_path in enumerate(self._network_paths):
            if network_path.addr == addr:
                return network_path

        # new network path
        network_path = QuicNetworkPath(addr)
        self._logger.info("Network path %s discovered", network_path.addr)
        return network_path

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
            self._logger.info("Stream %d created by peer" % stream_id)
            stream = self.streams[stream_id] = QuicStream(
                connection=self,
                stream_id=stream_id,
                max_stream_data_local=max_stream_data_local,
                max_stream_data_remote=max_stream_data_remote,
            )
            self._stream_handler(stream.reader, stream.writer)
        return stream

    def _initialize(self, peer_cid: bytes) -> None:
        # TLS
        self.tls = tls.Context(is_client=self.is_client, logger=self._logger)
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

        # TLS session resumption
        if (
            self.is_client
            and self._session_ticket is not None
            and self._session_ticket.is_valid
            and self._session_ticket.server_name == self.server_name
        ):
            self.tls.session_ticket = self._session_ticket

            # parse saved QUIC transport parameters - for 0-RTT
            if self._session_ticket.max_early_data_size == 0xFFFFFFFF:
                for ext_type, ext_data in self._session_ticket.other_extensions:
                    if ext_type == tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS:
                        self._parse_transport_parameters(
                            ext_data, from_session_ticket=True
                        )
                        break

        # TLS callbacks
        if self._session_ticket_fetcher is not None:
            self.tls.get_session_ticket_cb = self._session_ticket_fetcher
        if self._session_ticket_handler is not None:
            self.tls.new_session_ticket_cb = self._session_ticket_handler
        self.tls.update_traffic_key_cb = self._update_traffic_key

        # packet spaces
        self.cryptos = {
            tls.Epoch.INITIAL: CryptoPair(),
            tls.Epoch.ZERO_RTT: CryptoPair(),
            tls.Epoch.HANDSHAKE: CryptoPair(),
            tls.Epoch.ONE_RTT: CryptoPair(),
        }
        self.send_buffer = {
            tls.Epoch.INITIAL: Buffer(capacity=4096),
            tls.Epoch.HANDSHAKE: Buffer(capacity=4096),
            tls.Epoch.ONE_RTT: Buffer(capacity=4096),
        }
        self.spaces = {
            tls.Epoch.INITIAL: QuicPacketSpace(),
            tls.Epoch.HANDSHAKE: QuicPacketSpace(),
            tls.Epoch.ONE_RTT: QuicPacketSpace(),
        }
        self.streams[tls.Epoch.INITIAL] = QuicStream()
        self.streams[tls.Epoch.HANDSHAKE] = QuicStream()
        self.streams[tls.Epoch.ONE_RTT] = QuicStream()

        self.cryptos[tls.Epoch.INITIAL].setup_initial(
            cid=peer_cid, is_client=self.is_client
        )

        self._loss.spaces = list(self.spaces.values())
        self._packet_number = 0

    def _handle_ack_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle an ACK frame.
        """
        ack_rangeset, ack_delay_encoded = pull_ack_frame(buf)
        if frame_type == QuicFrameType.ACK_ECN:
            pull_uint_var(buf)
            pull_uint_var(buf)
            pull_uint_var(buf)

        self._loss.on_ack_received(
            space=self.spaces[context.epoch],
            ack_rangeset=ack_rangeset,
            ack_delay_encoded=ack_delay_encoded,
            now=context.time,
        )

        # check if we can discard handshake keys
        if (
            not self._handshake_confirmed
            and self._handshake_complete
            and context.epoch == tls.Epoch.ONE_RTT
        ):
            self._discard_epoch(tls.Epoch.HANDSHAKE)
            self._handshake_confirmed = True

    def _handle_connection_close_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a CONNECTION_CLOSE frame.
        """
        if frame_type == QuicFrameType.TRANSPORT_CLOSE:
            error_code, frame_type, reason_phrase = pull_transport_close_frame(buf)
        else:
            error_code, reason_phrase = pull_application_close_frame(buf)
            frame_type = None
        self._logger.info(
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
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a CRYPTO frame.
        """
        stream = self.streams[context.epoch]
        stream.add_frame(pull_crypto_frame(buf))
        data = stream.pull_data()
        if data:
            # pass data to TLS layer
            try:
                self.tls.handle_message(data, self.send_buffer)
                self._push_crypto_data()
            except tls.Alert as exc:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.CRYPTO_ERROR + int(exc.description),
                    frame_type=QuicFrameType.CRYPTO,
                    reason_phrase=str(exc),
                )

            # parse transport parameters
            if (
                not self._parameters_received
                and self.tls.received_extensions is not None
            ):
                for ext_type, ext_data in self.tls.received_extensions:
                    if ext_type == tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS:
                        self._parse_transport_parameters(ext_data)
                        self._parameters_received = True
                        break
                assert (
                    self._parameters_received
                ), "No QUIC transport parameters received"
                self._logger.info("ALPN negotiated protocol %s", self.alpn_protocol)

            # update current epoch
            if not self._handshake_complete and self.tls.state in [
                tls.State.CLIENT_POST_HANDSHAKE,
                tls.State.SERVER_POST_HANDSHAKE,
            ]:
                self._handshake_complete = True
                self._replenish_connection_ids()
                # wakeup waiter
                if not self._connected.is_set():
                    self._connected.set()

    def _handle_data_blocked_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a DATA_BLOCKED frame.
        """
        pull_uint_var(buf)  # limit

    def _handle_max_data_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_DATA frame.

        This adjusts the total amount of we can send to the peer.
        """
        max_data = pull_uint_var(buf)
        if max_data > self._remote_max_data:
            self._logger.info("Remote max_data raised to %d", max_data)
            self._remote_max_data = max_data

    def _handle_max_stream_data_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
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
            self._logger.info(
                "Stream %d remote max_stream_data raised to %d",
                stream_id,
                max_stream_data,
            )
            stream.max_stream_data_remote = max_stream_data

    def _handle_max_streams_bidi_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAMS_BIDI frame.

        This raises number of bidirectional streams we can initiate to the peer.
        """
        max_streams = pull_uint_var(buf)
        if max_streams > self._remote_max_streams_bidi:
            self._logger.info("Remote max_streams_bidi raised to %d", max_streams)
            self._remote_max_streams_bidi = max_streams

    def _handle_max_streams_uni_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAMS_UNI frame.

        This raises number of unidirectional streams we can initiate to the peer.
        """
        max_streams = pull_uint_var(buf)
        if max_streams > self._remote_max_streams_uni:
            self._logger.info("Remote max_streams_uni raised to %d", max_streams)
            self._remote_max_streams_uni = max_streams

    def _handle_new_connection_id_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a NEW_CONNECTION_ID frame.
        """
        sequence_number, cid, stateless_reset_token = pull_new_connection_id_frame(buf)
        self._logger.info(
            "New connection ID received %d %s", sequence_number, dump_cid(cid)
        )
        self._peer_cid_available.append(
            QuicConnectionId(
                cid=cid,
                sequence_number=sequence_number,
                stateless_reset_token=stateless_reset_token,
            )
        )

    def _handle_new_token_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a NEW_TOKEN frame.
        """
        pull_new_token_frame(buf)

    def _handle_padding_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PADDING or PING frame.
        """
        pass

    def _handle_path_challenge_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PATH_CHALLENGE frame.
        """
        data = pull_bytes(buf, 8)
        context.network_path.remote_challenge = data

    def _handle_path_response_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PATH_RESPONSE frame.
        """
        data = pull_bytes(buf, 8)
        if data != context.network_path.local_challenge:
            raise QuicConnectionError(
                error_code=QuicErrorCode.PROTOCOL_VIOLATION,
                frame_type=frame_type,
                reason_phrase="Response does not match challenge",
            )
        self._logger.info(
            "Network path %s validated by challenge", context.network_path.addr
        )
        context.network_path.is_validated = True

    def _handle_reset_stream_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a RESET_STREAM frame.
        """
        stream_id = pull_uint_var(buf)
        error_code = pull_uint16(buf)
        final_size = pull_uint_var(buf)

        # check stream direction
        self._assert_stream_can_receive(frame_type, stream_id)

        self._logger.info(
            "Stream %d reset by peer (error code %d, final size %d)",
            stream_id,
            error_code,
            final_size,
        )
        stream = self._get_or_create_stream(frame_type, stream_id)
        stream.connection_lost(None)

    def _handle_retire_connection_id_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a RETIRE_CONNECTION_ID frame.
        """
        sequence_number = pull_uint_var(buf)

        # find the connection ID by sequence number
        for index, connection_id in enumerate(self._host_cids):
            if connection_id.sequence_number == sequence_number:
                if connection_id.cid == context.host_cid:
                    raise QuicConnectionError(
                        error_code=QuicErrorCode.PROTOCOL_VIOLATION,
                        frame_type=frame_type,
                        reason_phrase="Cannot retire current connection ID",
                    )
                del self._host_cids[index]
                self._connection_id_retired_handler(connection_id.cid)
                break

        # issue a new connection ID
        self._replenish_connection_ids()

    def _handle_stop_sending_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
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
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAM frame.
        """
        stream_id = pull_uint_var(buf)
        if frame_type & 4:
            offset = pull_uint_var(buf)
        else:
            offset = 0
        if frame_type & 2:
            length = pull_uint_var(buf)
        else:
            length = buf.capacity - buf.tell()
        frame = QuicStreamFrame(
            offset=offset, data=pull_bytes(buf, length), fin=bool(frame_type & 1)
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
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
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
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAMS_BLOCKED frame.
        """
        pull_uint_var(buf)  # limit

    def _on_max_data_delivery(self, delivery: QuicDeliveryState) -> None:
        if delivery != QuicDeliveryState.ACKED:
            self._local_max_data_sent = 0

    def _on_max_stream_data_delivery(
        self, delivery: QuicDeliveryState, stream: QuicStream
    ) -> None:
        if delivery != QuicDeliveryState.ACKED:
            stream.max_stream_data_local_sent = 0

    def _on_new_connection_id_delivery(
        self, delivery: QuicDeliveryState, connection_id: QuicConnectionId
    ) -> None:
        """
        Callback when a NEW_CONNECTION_ID frame is acknowledged or lost.
        """
        if delivery != QuicDeliveryState.ACKED:
            connection_id.was_sent = False

    def _on_ping_delivery(self, delivery: QuicDeliveryState) -> None:
        """
        Callback when a PING frame is is acknowledged or lost.
        """
        if delivery == QuicDeliveryState.ACKED:
            self._logger.info("Received PING response")
            waiter = self._ping_waiter
            self._ping_waiter = None
            waiter.set_result(None)
        else:
            self._ping_pending = True

    def _on_retire_connection_id_delivery(
        self, delivery: QuicDeliveryState, sequence_number: int
    ) -> None:
        """
        Callback when a RETIRE_CONNECTION_ID frame is is acknowledged or lost.
        """
        if delivery != QuicDeliveryState.ACKED:
            self._retire_connection_ids.append(sequence_number)

    def _on_timeout(self) -> None:
        self._timer = None
        self._timer_at = None

        # idle timeout
        if self._loop.time() >= self._idle_timeout_at:
            self._logger.info("Idle timeout expired")
            self._set_state(QuicConnectionState.DRAINING)
            self.connection_lost(None)
            for epoch in self.spaces.keys():
                self._discard_epoch(epoch)
            return

        # loss detection timeout
        self._loss.on_loss_detection_timeout(now=self._loop.time())
        self._send_pending()

    def _payload_received(
        self, context: QuicReceiveContext, plain: bytes
    ) -> Tuple[bool, bool]:
        """
        Handle a QUIC packet payload.
        """
        buf = Buffer(data=plain)

        is_ack_eliciting = False
        is_probing = None
        while not buf.eof():
            frame_type = pull_uint_var(buf)

            # check frame type is known
            try:
                frame_handler, frame_epochs = self.__frame_handlers[frame_type]
            except IndexError:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.PROTOCOL_VIOLATION,
                    frame_type=frame_type,
                    reason_phrase="Unknown frame type",
                )

            # check frame is allowed for the epoch
            if context.epoch not in frame_epochs:
                raise QuicConnectionError(
                    error_code=QuicErrorCode.PROTOCOL_VIOLATION,
                    frame_type=frame_type,
                    reason_phrase="Unexpected frame type",
                )

            # handle the frame
            if frame_type != QuicFrameType.PADDING:
                try:
                    frame_handler(context, frame_type, buf)
                except BufferReadError:
                    raise QuicConnectionError(
                        error_code=QuicErrorCode.FRAME_ENCODING_ERROR,
                        frame_type=frame_type,
                        reason_phrase="Failed to parse frame",
                    )

            # update ACK only / probing flags
            if frame_type not in NON_ACK_ELICITING_FRAME_TYPES:
                is_ack_eliciting = True

            if frame_type not in PROBING_FRAME_TYPES:
                is_probing = False
            elif is_probing is None:
                is_probing = True

        return is_ack_eliciting, bool(is_probing)

    def _replenish_connection_ids(self) -> None:
        """
        Generate new connection IDs.
        """
        while len(self._host_cids) < 8:
            self._host_cids.append(
                QuicConnectionId(
                    cid=os.urandom(8),
                    sequence_number=self._host_cid_seq,
                    stateless_reset_token=os.urandom(16),
                )
            )
            self._host_cid_seq += 1

    def _push_crypto_data(self) -> None:
        for epoch, buf in self.send_buffer.items():
            self.streams[epoch].write(buf.data)
            buf.seek(0)

    def _send_pending(self) -> None:
        network_path = self._network_paths[0]

        self.__send_pending_task = None
        if self.__state == QuicConnectionState.DRAINING:
            return

        # build datagrams
        builder = QuicPacketBuilder(
            host_cid=self.host_cid,
            packet_number=self._packet_number,
            pad_first_datagram=(
                self.is_client and self.__state == QuicConnectionState.FIRSTFLIGHT
            ),
            peer_cid=self.peer_cid,
            peer_token=self.peer_token,
            spin_bit=self._spin_bit,
            version=self._version,
        )
        if self.__close:
            for epoch, packet_type in (
                (tls.Epoch.ONE_RTT, PACKET_TYPE_ONE_RTT),
                (tls.Epoch.HANDSHAKE, PACKET_TYPE_HANDSHAKE),
                (tls.Epoch.INITIAL, PACKET_TYPE_INITIAL),
            ):
                crypto = self.cryptos[epoch]
                if crypto.send.is_valid():
                    builder.start_packet(packet_type, crypto)
                    write_close_frame(builder, **self.__close)
                    builder.end_packet()
                    self.__close = None
                    break
        else:
            if not self._handshake_confirmed:
                for epoch in [tls.Epoch.INITIAL, tls.Epoch.HANDSHAKE]:
                    self._write_handshake(builder, epoch)
            self._write_application(builder, network_path)
        datagrams, packets = builder.flush()

        if datagrams:
            self._packet_number = builder.packet_number

            # send datagrams
            for datagram in datagrams:
                self._transport.sendto(datagram, network_path.addr)
                network_path.bytes_sent += len(datagram)

            # register packets
            now = self._loop.time()
            sent_handshake = False
            for packet in packets:
                packet.sent_time = now
                self._loss.on_packet_sent(
                    packet=packet, space=self.spaces[packet.epoch]
                )
                if packet.epoch == tls.Epoch.HANDSHAKE:
                    sent_handshake = True

            # check if we can discard initial keys
            if sent_handshake and self.is_client:
                self._discard_epoch(tls.Epoch.INITIAL)

        # arm timer
        self._set_timer()

    def _send_probe(self) -> None:
        self._probe_pending = True

    def _send_soon(self) -> None:
        if self.__send_pending_task is None:
            self.__send_pending_task = self._loop.call_soon(self._send_pending)

    def _parse_transport_parameters(
        self, data: bytes, from_session_ticket: bool = False
    ) -> None:
        quic_transport_parameters = pull_quic_transport_parameters(Buffer(data=data))

        # validate remote parameters
        if (
            self.is_client
            and not from_session_ticket
            and (
                quic_transport_parameters.original_connection_id
                != self._original_connection_id
            )
        ):
            raise QuicConnectionError(
                error_code=QuicErrorCode.TRANSPORT_PARAMETER_ERROR,
                frame_type=QuicFrameType.CRYPTO,
                reason_phrase="original_connection_id does not match",
            )

        # store remote parameters
        if quic_transport_parameters.idle_timeout is not None:
            self._remote_idle_timeout = quic_transport_parameters.idle_timeout / 1000.0
        for param in ["ack_delay_exponent", "max_ack_delay"]:
            value = getattr(quic_transport_parameters, param)
            if value is not None:
                setattr(self._loss, param, value)
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

        # wakeup waiters
        if not self._parameters_available.is_set():
            self._parameters_available.set()

    def _serialize_transport_parameters(self) -> bytes:
        quic_transport_parameters = QuicTransportParameters(
            idle_timeout=int(self._local_idle_timeout * 1000),
            initial_max_data=self._local_max_data,
            initial_max_stream_data_bidi_local=self._local_max_stream_data_bidi_local,
            initial_max_stream_data_bidi_remote=self._local_max_stream_data_bidi_remote,
            initial_max_stream_data_uni=self._local_max_stream_data_uni,
            initial_max_streams_bidi=self._local_max_streams_bidi,
            initial_max_streams_uni=self._local_max_streams_uni,
            ack_delay_exponent=10,
        )
        if not self.is_client:
            quic_transport_parameters.original_connection_id = (
                self._original_connection_id
            )

        buf = Buffer(capacity=512)
        push_quic_transport_parameters(buf, quic_transport_parameters)
        return buf.data

    def _set_state(self, state: QuicConnectionState) -> None:
        self._logger.info("%s -> %s", self.__state, state)
        self.__state = state

    def _set_timer(self) -> None:
        # determine earliest timeout
        if self.__state not in [
            QuicConnectionState.CLOSING,
            QuicConnectionState.DRAINING,
        ]:
            timer_at = self._idle_timeout_at
            loss_at = self._loss.get_loss_detection_time()
            if loss_at is not None and loss_at < timer_at:
                timer_at = loss_at
        else:
            timer_at = None

        # re-arm timer
        if self._timer is not None and self._timer_at != timer_at:
            self._timer.cancel()
            self._timer = None
        if self._timer is None and timer_at is not None:
            self._timer = self._loop.call_at(timer_at, self._on_timeout)
        self._timer_at = timer_at

    def _stream_can_receive(self, stream_id: int) -> bool:
        return stream_is_client_initiated(
            stream_id
        ) != self.is_client or not stream_is_unidirectional(stream_id)

    def _stream_can_send(self, stream_id: int) -> bool:
        return stream_is_client_initiated(
            stream_id
        ) == self.is_client or not stream_is_unidirectional(stream_id)

    def _update_traffic_key(
        self,
        direction: tls.Direction,
        epoch: tls.Epoch,
        cipher_suite: tls.CipherSuite,
        secret: bytes,
    ) -> None:
        """
        Callback which is invoked by the TLS engine when new traffic keys are
        available.
        """
        if self.secrets_log_file is not None:
            label_row = self.is_client == (direction == tls.Direction.DECRYPT)
            label = SECRETS_LABELS[label_row][epoch.value]
            self.secrets_log_file.write(
                "%s %s %s\n" % (label, self.tls.client_random.hex(), secret.hex())
            )
            self.secrets_log_file.flush()

        crypto = self.cryptos[epoch]
        if direction == tls.Direction.ENCRYPT:
            crypto.send.setup(cipher_suite, secret)
        else:
            crypto.recv.setup(cipher_suite, secret)

    def _write_application(
        self, builder: QuicPacketBuilder, network_path: QuicNetworkPath
    ) -> None:
        crypto_stream_id: Optional[tls.Epoch] = None
        if self.cryptos[tls.Epoch.ONE_RTT].send.is_valid():
            crypto = self.cryptos[tls.Epoch.ONE_RTT]
            crypto_stream_id = tls.Epoch.ONE_RTT
            packet_type = PACKET_TYPE_ONE_RTT
        elif self.cryptos[tls.Epoch.ZERO_RTT].send.is_valid():
            crypto = self.cryptos[tls.Epoch.ZERO_RTT]
            packet_type = PACKET_TYPE_ZERO_RTT
        else:
            return
        space = self.spaces[tls.Epoch.ONE_RTT]

        buf = builder.buffer

        while (
            builder.flight_bytes + self._loss.bytes_in_flight
            < self._loss.congestion_window
        ) or self._probe_pending:
            # write header
            builder.start_packet(packet_type, crypto)

            if self._handshake_complete:
                # ACK
                if space.ack_required and space.ack_queue:
                    builder.start_frame(QuicFrameType.ACK)
                    push_ack_frame(buf, space.ack_queue, 0)
                    space.ack_required = False

                # PATH CHALLENGE
                if (
                    not network_path.is_validated
                    and network_path.local_challenge is None
                ):
                    self._logger.info(
                        "Network path %s sending challenge", network_path.addr
                    )
                    network_path.local_challenge = os.urandom(8)
                    builder.start_frame(QuicFrameType.PATH_CHALLENGE)
                    push_bytes(buf, network_path.local_challenge)

                # PATH RESPONSE
                if network_path.remote_challenge is not None:
                    builder.start_frame(QuicFrameType.PATH_RESPONSE)
                    push_bytes(buf, network_path.remote_challenge)
                    network_path.remote_challenge = None

                # NEW_CONNECTION_ID
                for connection_id in self._host_cids:
                    if not connection_id.was_sent:
                        builder.start_frame(
                            QuicFrameType.NEW_CONNECTION_ID,
                            self._on_new_connection_id_delivery,
                            (connection_id,),
                        )
                        push_new_connection_id_frame(
                            buf,
                            connection_id.sequence_number,
                            connection_id.cid,
                            connection_id.stateless_reset_token,
                        )
                        connection_id.was_sent = True
                        self._connection_id_issued_handler(connection_id.cid)

                # RETIRE_CONNECTION_ID
                while self._retire_connection_ids:
                    sequence_number = self._retire_connection_ids.pop(0)
                    builder.start_frame(
                        QuicFrameType.RETIRE_CONNECTION_ID,
                        self._on_retire_connection_id_delivery,
                        (sequence_number,),
                    )
                    push_uint_var(buf, sequence_number)

                # connection-level limits
                self._write_connection_limits(builder=builder, space=space)

            # stream-level limits
            for stream_id, stream in self.streams.items():
                if isinstance(stream_id, int):
                    self._write_stream_limits(
                        builder=builder, space=space, stream=stream
                    )

            # PING (user-request)
            if self._ping_pending:
                self._logger.info("Sending PING in packet %d", builder.packet_number)
                builder.start_frame(QuicFrameType.PING, self._on_ping_delivery)
                self._ping_pending = False

            # PING (probe)
            if self._probe_pending:
                self._logger.info("Sending probe")
                builder.start_frame(QuicFrameType.PING)
                self._probe_pending = False

            for stream_id, stream in self.streams.items():
                # CRYPTO
                if stream_id == crypto_stream_id:
                    write_crypto_frame(builder=builder, space=space, stream=stream)

                # STREAM
                elif isinstance(stream_id, int):
                    self._remote_max_data_used += write_stream_frame(
                        builder=builder,
                        space=space,
                        stream=stream,
                        max_offset=min(
                            stream._send_highest
                            + self._remote_max_data
                            - self._remote_max_data_used,
                            stream.max_stream_data_remote,
                        ),
                    )

            if not builder.end_packet():
                break

    def _write_handshake(self, builder: QuicPacketBuilder, epoch: tls.Epoch) -> None:
        crypto = self.cryptos[epoch]
        if not crypto.send.is_valid():
            return

        buf = builder.buffer
        space = self.spaces[epoch]

        while (
            builder.flight_bytes + self._loss.bytes_in_flight
            < self._loss.congestion_window
        ):
            if epoch == tls.Epoch.INITIAL:
                packet_type = PACKET_TYPE_INITIAL
            else:
                packet_type = PACKET_TYPE_HANDSHAKE
            builder.start_packet(packet_type, crypto)

            # ACK
            if space.ack_required and space.ack_queue:
                builder.start_frame(QuicFrameType.ACK)
                push_ack_frame(buf, space.ack_queue, 0)
                space.ack_required = False

            # CRYPTO
            write_crypto_frame(builder=builder, space=space, stream=self.streams[epoch])

            if not builder.end_packet():
                break

    def _write_connection_limits(
        self, builder: QuicPacketBuilder, space: QuicPacketSpace
    ) -> None:
        # raise MAX_DATA if needed
        if self._local_max_data_used + MAX_DATA_WINDOW // 2 > self._local_max_data:
            self._local_max_data += MAX_DATA_WINDOW
            self._logger.info("Local max_data raised to %d", self._local_max_data)
        if self._local_max_data_sent != self._local_max_data:
            builder.start_frame(QuicFrameType.MAX_DATA, self._on_max_data_delivery)
            push_uint_var(builder.buffer, self._local_max_data)
            self._local_max_data_sent = self._local_max_data

    def _write_stream_limits(
        self, builder: QuicPacketBuilder, space: QuicPacketSpace, stream: QuicStream
    ) -> None:
        # raise MAX_STREAM_DATA if needed
        if stream._recv_highest + MAX_DATA_WINDOW // 2 > stream.max_stream_data_local:
            stream.max_stream_data_local += MAX_DATA_WINDOW
            self._logger.info(
                "Stream %d local max_stream_data raised to %d",
                stream.stream_id,
                stream.max_stream_data_local,
            )
        if stream.max_stream_data_local_sent != stream.max_stream_data_local:
            builder.start_frame(
                QuicFrameType.MAX_STREAM_DATA,
                self._on_max_stream_data_delivery,
                (stream,),
            )
            push_uint_var(builder.buffer, stream.stream_id)
            push_uint_var(builder.buffer, stream.max_stream_data_local)
            stream.max_stream_data_local_sent = stream.max_stream_data_local
