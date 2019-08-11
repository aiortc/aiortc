import binascii
import logging
import os
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, FrozenSet, List, Optional, Sequence, Tuple, cast

from .. import tls
from ..buffer import Buffer, BufferReadError, size_uint_var
from . import events
from .configuration import QuicConfiguration
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
from .packet_builder import (
    PACKET_MAX_SIZE,
    QuicDeliveryState,
    QuicPacketBuilder,
    QuicPacketBuilderStop,
)
from .recovery import K_GRANULARITY, QuicPacketRecovery, QuicPacketSpace
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
        buf.push_uint_var(error_code)
        buf.push_uint_var(len(reason_bytes))
        buf.push_bytes(reason_bytes)
    else:
        builder.start_frame(QuicFrameType.TRANSPORT_CLOSE)
        buf.push_uint_var(error_code)
        buf.push_uint_var(frame_type)
        buf.push_uint_var(len(reason_bytes))
        buf.push_bytes(reason_bytes)


def write_crypto_frame(
    builder: QuicPacketBuilder, space: QuicPacketSpace, stream: QuicStream
) -> None:
    buf = builder.buffer

    frame_overhead = 3 + size_uint_var(stream.next_send_offset)
    frame = stream.get_frame(builder.remaining_space - frame_overhead)
    if frame is not None:
        builder.start_frame(
            QuicFrameType.CRYPTO,
            stream.on_data_delivery,
            (frame.offset, frame.offset + len(frame.data)),
        )
        buf.push_uint_var(frame.offset)
        buf.push_uint16(len(frame.data) | 0x4000)
        buf.push_bytes(frame.data)


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
        buf.push_uint_var(stream.stream_id)
        if frame.offset:
            buf.push_uint_var(frame.offset)
        buf.push_uint16(len(frame.data) | 0x4000)
        buf.push_bytes(frame.data)
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
    TERMINATED = 4


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
    quic_logger_frames: Optional[List[Any]]
    time: float


END_STATES = frozenset(
    [
        QuicConnectionState.CLOSING,
        QuicConnectionState.DRAINING,
        QuicConnectionState.TERMINATED,
    ]
)


class QuicConnection:
    """
    A QUIC connection.

    :param configuration: The QUIC configuration to use.
    """

    def __init__(
        self,
        *,
        configuration: QuicConfiguration,
        original_connection_id: Optional[bytes] = None,
        session_ticket_fetcher: Optional[tls.SessionTicketFetcher] = None,
        session_ticket_handler: Optional[tls.SessionTicketHandler] = None,
    ) -> None:
        if configuration.is_client:
            assert (
                original_connection_id is None
            ), "Cannot set original_connection_id for a client"
        else:
            assert (
                configuration.certificate is not None
            ), "SSL certificate is required for a server"
            assert (
                configuration.private_key is not None
            ), "SSL private key is required for a server"

        # counters for debugging
        self._quic_logger = configuration.quic_logger
        self._stateless_retry_count = 0

        # configuration
        self._configuration = configuration
        self._is_client = configuration.is_client

        self._ack_delay = K_GRANULARITY
        self._close_at: Optional[float] = None
        self._close_event: Optional[events.ConnectionTerminated] = None
        self._connect_called = False
        self._cryptos: Dict[tls.Epoch, CryptoPair] = {}
        self._crypto_buffers: Dict[tls.Epoch, Buffer] = {}
        self._crypto_streams: Dict[tls.Epoch, QuicStream] = {}
        self._events: Deque[events.QuicEvent] = deque()
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
        self._local_active_connection_id_limit = 8
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
            is_client_without_1rtt=self._is_client,
            quic_logger=self._quic_logger,
            send_probe=self._send_probe,
        )
        self._loss_at: Optional[float] = None
        self._network_paths: List[QuicNetworkPath] = []
        self._original_connection_id = original_connection_id
        self._packet_number = 0
        self._parameters_received = False
        self._peer_cid = os.urandom(8)
        self._peer_cid_seq: Optional[int] = None
        self._peer_cid_available: List[QuicConnectionId] = []
        self._peer_token = b""
        self._remote_active_connection_id_limit = 0
        self._remote_idle_timeout = 0.0  # seconds
        self._remote_max_data = 0
        self._remote_max_data_used = 0
        self._remote_max_stream_data_bidi_local = 0
        self._remote_max_stream_data_bidi_remote = 0
        self._remote_max_stream_data_uni = 0
        self._remote_max_streams_bidi = 0
        self._remote_max_streams_uni = 0
        self._spaces: Dict[tls.Epoch, QuicPacketSpace] = {}
        self._spin_bit = False
        self._spin_highest_pn = 0
        self._state = QuicConnectionState.FIRSTFLIGHT
        self._streams: Dict[int, QuicStream] = {}
        self._streams_blocked_bidi: List[QuicStream] = []
        self._streams_blocked_uni: List[QuicStream] = []
        self._version: Optional[int] = None

        # things to send
        self._close_pending = False
        self._ping_pending: List[int] = []
        self._probe_pending = False
        self._retire_connection_ids: List[int] = []
        self._streams_blocked_pending = False

        # callbacks
        self._session_ticket_fetcher = session_ticket_fetcher
        self._session_ticket_handler = session_ticket_handler

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
    def configuration(self) -> QuicConfiguration:
        return self._configuration

    def change_connection_id(self) -> None:
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
            self._peer_cid = connection_id.cid
            self._logger.info(
                "Migrating to %s (%d)", dump_cid(self._peer_cid), self._peer_cid_seq
            )

    def close(
        self,
        error_code: int = QuicErrorCode.NO_ERROR,
        frame_type: Optional[int] = None,
        reason_phrase: str = "",
    ) -> None:
        """
        Close the connection.

        :param error_code: An error code indicating why the connection is
                           being closed.
        :param reason_phrase: A human-readable explanation of why the
                              connection is being closed.
        """
        if self._state not in END_STATES:
            self._close_event = events.ConnectionTerminated(
                error_code=error_code,
                frame_type=frame_type,
                reason_phrase=reason_phrase,
            )
            self._close_pending = True

    def connect(self, addr: NetworkAddress, now: float) -> None:
        """
        Initiate the TLS handshake.

        This method can only be called for clients and a single time.

        After calling this method call :meth:`datagrams_to_send` to retrieve data
        which needs to be sent.

        :param addr: The network address of the remote peer.
        :param now: The current time.
        """
        assert (
            self._is_client and not self._connect_called
        ), "connect() can only be called for clients and a single time"
        self._connect_called = True

        self._network_paths = [QuicNetworkPath(addr, is_validated=True)]
        self._version = self._configuration.supported_versions[0]
        self._connect(now=now)

    def datagrams_to_send(self, now: float) -> List[Tuple[bytes, NetworkAddress]]:
        """
        Return a list of `(data, addr)` tuples of datagrams which need to be
        sent, and the network address to which they need to be sent.

        :param now: The current time.
        """
        network_path = self._network_paths[0]

        if self._state in END_STATES:
            return []

        # build datagrams
        builder = QuicPacketBuilder(
            host_cid=self.host_cid,
            packet_number=self._packet_number,
            pad_first_datagram=(
                self._is_client and self._state == QuicConnectionState.FIRSTFLIGHT
            ),
            peer_cid=self._peer_cid,
            peer_token=self._peer_token,
            spin_bit=self._spin_bit,
            version=self._version,
        )
        if self._close_pending:
            for epoch, packet_type in (
                (tls.Epoch.ONE_RTT, PACKET_TYPE_ONE_RTT),
                (tls.Epoch.HANDSHAKE, PACKET_TYPE_HANDSHAKE),
                (tls.Epoch.INITIAL, PACKET_TYPE_INITIAL),
            ):
                crypto = self._cryptos[epoch]
                if crypto.send.is_valid():
                    builder.start_packet(packet_type, crypto)
                    write_close_frame(
                        builder=builder,
                        error_code=self._close_event.error_code,
                        frame_type=self._close_event.frame_type,
                        reason_phrase=self._close_event.reason_phrase,
                    )
                    builder.end_packet()
                    self._close_pending = False
                    break
            self._close_begin(is_initiator=True, now=now)
        else:
            # congestion control
            builder.max_flight_bytes = (
                self._loss.congestion_window - self._loss.bytes_in_flight
            )
            if self._probe_pending and builder.max_flight_bytes < PACKET_MAX_SIZE:
                builder.max_flight_bytes = PACKET_MAX_SIZE

            # limit data on un-validated network paths
            if not network_path.is_validated:
                builder.max_total_bytes = (
                    network_path.bytes_received * 3 - network_path.bytes_sent
                )

            try:
                if not self._handshake_confirmed:
                    for epoch in [tls.Epoch.INITIAL, tls.Epoch.HANDSHAKE]:
                        self._write_handshake(builder, epoch)
                self._write_application(builder, network_path, now)
            except QuicPacketBuilderStop:
                pass

        datagrams, packets = builder.flush()

        if datagrams:
            self._packet_number = builder.packet_number

            # register packets
            sent_handshake = False
            for packet in packets:
                packet.sent_time = now
                self._loss.on_packet_sent(
                    packet=packet, space=self._spaces[packet.epoch]
                )
                if packet.epoch == tls.Epoch.HANDSHAKE:
                    sent_handshake = True

                # log packet
                if self._quic_logger is not None:
                    self._quic_logger.log_event(
                        category="transport",
                        event="packet_sent",
                        data={
                            "type": self._quic_logger.packet_type(packet.packet_type),
                            "header": {
                                "packet_number": packet.packet_number,
                                "packet_size": packet.sent_bytes,
                            },
                            "frames": [],
                        },
                    )

            # check if we can discard initial keys
            if sent_handshake and self._is_client:
                self._discard_epoch(tls.Epoch.INITIAL)

        # return datagrams to send and the destination network address
        ret = []
        for datagram in datagrams:
            byte_length = len(datagram)
            network_path.bytes_sent += byte_length
            ret.append((datagram, network_path.addr))

            if self._quic_logger is not None:
                self._quic_logger.log_event(
                    category="transport",
                    event="datagram_sent",
                    data={"byte_length": byte_length, "count": 1},
                )
        return ret

    def get_next_available_stream_id(self, is_unidirectional=False) -> int:
        """
        Return the stream ID for the next stream created by this endpoint.
        """
        stream_id = (int(is_unidirectional) << 1) | int(not self._is_client)
        while stream_id in self._streams:
            stream_id += 4
        return stream_id

    def get_timer(self) -> Optional[float]:
        """
        Return the time at which the timer should fire or None if no timer is needed.
        """
        timer_at = self._close_at
        if self._state not in END_STATES:
            # ack timer
            for space in self._loss.spaces:
                if space.ack_at is not None and space.ack_at < timer_at:
                    timer_at = space.ack_at

            # loss detection timer
            self._loss_at = self._loss.get_loss_detection_time()
            if self._loss_at is not None and self._loss_at < timer_at:
                timer_at = self._loss_at
        return timer_at

    def handle_timer(self, now: float) -> None:
        """
        Handle the timer.

        After calling this method call :meth:`datagrams_to_send` to retrieve data
        which needs to be sent.

        :param now: The current time.
        """
        # end of closing period or idle timeout
        if now >= self._close_at:
            if self._close_event is None:
                self._close_event = events.ConnectionTerminated(
                    error_code=QuicErrorCode.INTERNAL_ERROR,
                    frame_type=None,
                    reason_phrase="Idle timeout",
                )
            self._close_end()
            return

        # loss detection timeout
        if self._loss_at is not None and now >= self._loss_at:
            self._logger.debug("Loss detection triggered")
            self._loss.on_loss_detection_timeout(now=now)

    def next_event(self) -> Optional[events.QuicEvent]:
        """
        Retrieve the next event from the event buffer.

        Returns `None` if there are no buffered events.
        """
        try:
            return self._events.popleft()
        except IndexError:
            return None

    def receive_datagram(self, data: bytes, addr: NetworkAddress, now: float) -> None:
        """
        Handle an incoming datagram.

        :param data: The datagram which was received.
        :param addr: The network address from which the datagram was received.
        :param now: The current time.
        """
        # stop handling packets when closing
        if self._state in END_STATES:
            return

        data = cast(bytes, data)
        if self._quic_logger is not None:
            self._quic_logger.log_event(
                category="transport",
                event="datagram_received",
                data={"byte_length": len(data), "count": 1},
            )

        buf = Buffer(data=data)
        while not buf.eof():
            start_off = buf.tell()
            try:
                header = pull_quic_header(buf, host_cid_length=len(self.host_cid))
            except ValueError:
                return

            # check destination CID matches
            destination_cid_seq: Optional[int] = None
            for connection_id in self._host_cids:
                if header.destination_cid == connection_id.cid:
                    destination_cid_seq = connection_id.sequence_number
                    break
            if self._is_client and destination_cid_seq is None:
                return

            # check protocol version
            if self._is_client and header.version == QuicProtocolVersion.NEGOTIATION:
                # version negotiation
                versions = []
                while not buf.eof():
                    versions.append(buf.pull_uint32())
                if self._quic_logger is not None:
                    self._quic_logger.log_event(
                        category="transport",
                        event="packet_received",
                        data={
                            "type": "VERSION_NEGOTIATION",
                            "header": {},
                            "frames": [],
                        },
                    )
                common = set(self._configuration.supported_versions).intersection(
                    versions
                )
                if not common:
                    self._logger.error("Could not find a common protocol version")
                    self._close_event = events.ConnectionTerminated(
                        error_code=QuicErrorCode.INTERNAL_ERROR,
                        frame_type=None,
                        reason_phrase="Could not find a common protocol version",
                    )
                    self._close_end()
                    return
                self._version = QuicProtocolVersion(max(common))
                self._logger.info("Retrying with %s", self._version)
                self._connect(now=now)
                return
            elif (
                header.version is not None
                and header.version not in self._configuration.supported_versions
            ):
                # unsupported version
                return

            if self._is_client and header.packet_type == PACKET_TYPE_RETRY:
                # stateless retry
                if (
                    header.destination_cid == self.host_cid
                    and header.original_destination_cid == self._peer_cid
                    and not self._stateless_retry_count
                ):
                    if self._quic_logger is not None:
                        self._quic_logger.log_event(
                            category="transport",
                            event="packet_received",
                            data={"type": "RETRY", "header": {}, "frames": []},
                        )

                    self._original_connection_id = self._peer_cid
                    self._peer_cid = header.source_cid
                    self._peer_token = header.token
                    self._stateless_retry_count += 1
                    self._logger.info("Performing stateless retry")
                    self._connect(now=now)
                return

            network_path = self._find_network_path(addr)

            # server initialization
            if not self._is_client and self._state == QuicConnectionState.FIRSTFLIGHT:
                assert (
                    header.packet_type == PACKET_TYPE_INITIAL
                ), "first packet must be INITIAL"
                self._network_paths = [network_path]
                self._version = QuicProtocolVersion(header.version)
                self._initialize(header.destination_cid)

            # determine crypto and packet space
            epoch = get_epoch(header.packet_type)
            crypto = self._cryptos[epoch]
            if epoch == tls.Epoch.ZERO_RTT:
                space = self._spaces[tls.Epoch.ONE_RTT]
            else:
                space = self._spaces[epoch]

            # decrypt packet
            encrypted_off = buf.tell() - start_off
            end_off = buf.tell() + header.rest_length
            buf.seek(end_off)

            try:
                plain_header, plain_payload, packet_number = crypto.decrypt_packet(
                    data[start_off:end_off], encrypted_off, space.expected_packet_number
                )
            except CryptoError as exc:
                self._logger.debug(exc)
                continue
            if packet_number > space.expected_packet_number:
                space.expected_packet_number = packet_number + 1

            # log packet
            quic_logger_frames: Optional[List[Dict]] = None
            if self._quic_logger is not None:
                quic_logger_frames = []
                self._quic_logger.log_event(
                    category="transport",
                    event="packet_received",
                    data={
                        "type": self._quic_logger.packet_type(header.packet_type),
                        "header": {
                            "packet_number": packet_number,
                            "packet_size": end_off - start_off,
                        },
                        "frames": quic_logger_frames,
                    },
                )

            # discard initial keys and packet space
            if not self._is_client and epoch == tls.Epoch.HANDSHAKE:
                self._discard_epoch(tls.Epoch.INITIAL)

            # update state
            if self._peer_cid_seq is None:
                self._peer_cid = header.source_cid
                self._peer_cid_seq = 0

            if self._state == QuicConnectionState.FIRSTFLIGHT:
                self._set_state(QuicConnectionState.CONNECTED)

            # update spin bit
            if not header.is_long_header and packet_number > self._spin_highest_pn:
                spin_bit = get_spin_bit(plain_header[0])
                if self._is_client:
                    self._spin_bit = not spin_bit
                else:
                    self._spin_bit = spin_bit
                self._spin_highest_pn = packet_number

                if self._quic_logger is not None:
                    self._quic_logger.log_event(
                        category="connectivity",
                        event="spin_bit_update",
                        data={"state": self._spin_bit},
                    )

            # handle payload
            context = QuicReceiveContext(
                epoch=epoch,
                host_cid=header.destination_cid,
                network_path=network_path,
                quic_logger_frames=quic_logger_frames,
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
            if self._state in END_STATES or self._close_pending:
                return

            # update idle timeout
            self._close_at = now + self._configuration.idle_timeout

            # handle migration
            if (
                not self._is_client
                and context.host_cid != self.host_cid
                and epoch == tls.Epoch.ONE_RTT
            ):
                self._logger.info(
                    "Peer migrating to %s (%d)",
                    dump_cid(context.host_cid),
                    destination_cid_seq,
                )
                self.host_cid = context.host_cid
                self.change_connection_id()

            # update network path
            if not network_path.is_validated and epoch == tls.Epoch.HANDSHAKE:
                self._logger.info(
                    "Network path %s validated by handshake", network_path.addr
                )
                network_path.is_validated = True
            network_path.bytes_received += end_off - start_off
            if network_path not in self._network_paths:
                self._network_paths.append(network_path)
            idx = self._network_paths.index(network_path)
            if idx and not is_probing:
                self._logger.info("Network path %s promoted", network_path.addr)
                self._network_paths.pop(idx)
                self._network_paths.insert(0, network_path)

            # record packet as received
            if packet_number > space.largest_received_packet:
                space.largest_received_packet = packet_number
            space.ack_queue.add(packet_number)
            if is_ack_eliciting and space.ack_at is None:
                space.ack_at = now + self._ack_delay

    def request_key_update(self) -> None:
        """
        Request an update of the encryption keys.
        """
        assert self._handshake_complete, "cannot change key before handshake completes"
        self._cryptos[tls.Epoch.ONE_RTT].update_key()

    def send_ping(self, uid: int) -> None:
        """
        Send a PING frame to the peer.

        :param uid: A unique ID for this PING.
        """
        self._ping_pending.append(uid)

    def send_stream_data(
        self, stream_id: int, data: bytes, end_stream: bool = False
    ) -> None:
        """
        Send data on the specific stream.

        :param stream_id: The stream's ID.
        :param data: The data to be sent.
        :param end_stream: If set to `True`, the FIN bit will be set.
        """
        if stream_is_client_initiated(stream_id) != self._is_client:
            if stream_id not in self._streams:
                raise ValueError("Cannot send data on unknown peer-initiated stream")
            if stream_is_unidirectional(stream_id):
                raise ValueError(
                    "Cannot send data on peer-initiated unidirectional stream"
                )

        try:
            stream = self._streams[stream_id]
        except KeyError:
            self._create_stream(stream_id=stream_id)
            stream = self._streams[stream_id]
        stream.write(data, end_stream=end_stream)

    # Private

    def _alpn_handler(self, alpn_protocol: str) -> None:
        """
        Callback which is invoked by the TLS engine when ALPN negotiation completes.
        """
        self._events.append(events.ProtocolNegotiated(alpn_protocol=alpn_protocol))

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

    def _close_begin(self, is_initiator: bool, now: float) -> None:
        """
        Begin the close procedure.
        """
        self._close_at = now + 3 * self._loss.get_probe_timeout()
        if is_initiator:
            self._set_state(QuicConnectionState.CLOSING)
        else:
            self._set_state(QuicConnectionState.DRAINING)

    def _close_end(self) -> None:
        """
        End the close procedure.
        """
        self._close_at = None
        for epoch in self._spaces.keys():
            self._discard_epoch(epoch)
        self._events.append(self._close_event)
        self._set_state(QuicConnectionState.TERMINATED)

    def _connect(self, now: float) -> None:
        """
        Start the client handshake.
        """
        assert self._is_client

        self._close_at = now + self._configuration.idle_timeout
        self._initialize(self._peer_cid)

        self.tls.handle_message(b"", self._crypto_buffers)
        self._push_crypto_data()

    def _create_stream(self, stream_id: int) -> QuicStream:
        """
        Create a QUIC stream in order to send data to the peer.
        """
        # determine limits
        if stream_is_unidirectional(stream_id):
            max_stream_data_local = 0
            max_stream_data_remote = self._remote_max_stream_data_uni
            max_streams = self._remote_max_streams_uni
            streams_blocked = self._streams_blocked_uni
        else:
            max_stream_data_local = self._local_max_stream_data_bidi_local
            max_stream_data_remote = self._remote_max_stream_data_bidi_remote
            max_streams = self._remote_max_streams_bidi
            streams_blocked = self._streams_blocked_bidi

        # create stream
        stream = self._streams[stream_id] = QuicStream(
            connection=self,
            stream_id=stream_id,
            max_stream_data_local=max_stream_data_local,
            max_stream_data_remote=max_stream_data_remote,
        )

        # mark stream as blocked if needed
        if stream_id // 4 >= max_streams:
            stream.is_blocked = True
            streams_blocked.append(stream)
            self._streams_blocked_pending = True

        return stream

    def _discard_epoch(self, epoch: tls.Epoch) -> None:
        self._logger.debug("Discarding epoch %s", epoch)
        self._cryptos[epoch].teardown()
        self._loss.discard_space(self._spaces[epoch])

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
        stream = self._streams.get(stream_id, None)
        if stream is None:
            # check initiator
            if stream_is_client_initiated(stream_id) == self._is_client:
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
            stream = self._streams[stream_id] = QuicStream(
                connection=self,
                stream_id=stream_id,
                max_stream_data_local=max_stream_data_local,
                max_stream_data_remote=max_stream_data_remote,
            )
        return stream

    def _initialize(self, peer_cid: bytes) -> None:
        # TLS
        self.tls = tls.Context(is_client=self._is_client, logger=self._logger)
        self.tls.alpn_protocols = self._configuration.alpn_protocols
        self.tls.certificate = self._configuration.certificate
        self.tls.certificate_private_key = self._configuration.private_key
        self.tls.handshake_extensions = [
            (
                tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                self._serialize_transport_parameters(),
            )
        ]
        self.tls.server_name = self._configuration.server_name

        # TLS session resumption
        session_ticket = self._configuration.session_ticket
        if (
            self._is_client
            and session_ticket is not None
            and session_ticket.is_valid
            and session_ticket.server_name == self._configuration.server_name
        ):
            self.tls.session_ticket = self._configuration.session_ticket

            # parse saved QUIC transport parameters - for 0-RTT
            if session_ticket.max_early_data_size == 0xFFFFFFFF:
                for ext_type, ext_data in session_ticket.other_extensions:
                    if ext_type == tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS:
                        self._parse_transport_parameters(
                            ext_data, from_session_ticket=True
                        )
                        break

        # TLS callbacks
        self.tls.alpn_cb = self._alpn_handler
        if self._session_ticket_fetcher is not None:
            self.tls.get_session_ticket_cb = self._session_ticket_fetcher
        if self._session_ticket_handler is not None:
            self.tls.new_session_ticket_cb = self._session_ticket_handler
        self.tls.update_traffic_key_cb = self._update_traffic_key

        # packet spaces
        self._cryptos = {
            tls.Epoch.INITIAL: CryptoPair(),
            tls.Epoch.ZERO_RTT: CryptoPair(),
            tls.Epoch.HANDSHAKE: CryptoPair(),
            tls.Epoch.ONE_RTT: CryptoPair(),
        }
        self._crypto_buffers = {
            tls.Epoch.INITIAL: Buffer(capacity=4096),
            tls.Epoch.HANDSHAKE: Buffer(capacity=4096),
            tls.Epoch.ONE_RTT: Buffer(capacity=4096),
        }
        self._crypto_streams = {
            tls.Epoch.INITIAL: QuicStream(),
            tls.Epoch.HANDSHAKE: QuicStream(),
            tls.Epoch.ONE_RTT: QuicStream(),
        }
        self._spaces = {
            tls.Epoch.INITIAL: QuicPacketSpace(),
            tls.Epoch.HANDSHAKE: QuicPacketSpace(),
            tls.Epoch.ONE_RTT: QuicPacketSpace(),
        }

        self._cryptos[tls.Epoch.INITIAL].setup_initial(
            cid=peer_cid, is_client=self._is_client
        )

        self._loss.spaces = list(self._spaces.values())
        self._packet_number = 0

    def _handle_ack_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle an ACK frame.
        """
        ack_rangeset, ack_delay_encoded = pull_ack_frame(buf)
        if frame_type == QuicFrameType.ACK_ECN:
            buf.pull_uint_var()
            buf.pull_uint_var()
            buf.pull_uint_var()

        # log frame
        if context.quic_logger_frames is not None:
            context.quic_logger_frames.append(
                {
                    "ack_delay": str(
                        (ack_delay_encoded << self._loss.ack_delay_exponent) // 1000
                    ),
                    "acked_ranges": [[x.start, x.stop - 1] for x in ack_rangeset],
                    "type": "ack",
                }
            )

        self._loss.on_ack_received(
            space=self._spaces[context.epoch],
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
        self._close_event = events.ConnectionTerminated(
            error_code=error_code, frame_type=frame_type, reason_phrase=reason_phrase
        )
        self._close_begin(is_initiator=False, now=context.time)

    def _handle_crypto_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a CRYPTO frame.
        """
        stream = self._crypto_streams[context.epoch]
        stream.add_frame(pull_crypto_frame(buf))
        data = stream.pull_data()
        if data:
            # pass data to TLS layer
            try:
                self.tls.handle_message(data, self._crypto_buffers)
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

            # update current epoch
            if not self._handshake_complete and self.tls.state in [
                tls.State.CLIENT_POST_HANDSHAKE,
                tls.State.SERVER_POST_HANDSHAKE,
            ]:
                self._handshake_complete = True
                self._loss.is_client_without_1rtt = False
                self._replenish_connection_ids()
                self._events.append(
                    events.HandshakeCompleted(
                        alpn_protocol=self.tls.alpn_negotiated,
                        early_data_accepted=self.tls.early_data_accepted,
                        session_resumed=self.tls.session_resumed,
                    )
                )
                self._unblock_streams(is_unidirectional=False)
                self._unblock_streams(is_unidirectional=True)
                self._logger.info(
                    "ALPN negotiated protocol %s", self.tls.alpn_negotiated
                )

    def _handle_data_blocked_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a DATA_BLOCKED frame.
        """
        buf.pull_uint_var()  # limit

    def _handle_max_data_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_DATA frame.

        This adjusts the total amount of we can send to the peer.
        """
        max_data = buf.pull_uint_var()
        if max_data > self._remote_max_data:
            self._logger.debug("Remote max_data raised to %d", max_data)
            self._remote_max_data = max_data

    def _handle_max_stream_data_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAM_DATA frame.

        This adjusts the amount of data we can send on a specific stream.
        """
        stream_id = buf.pull_uint_var()
        max_stream_data = buf.pull_uint_var()

        # check stream direction
        self._assert_stream_can_send(frame_type, stream_id)

        stream = self._get_or_create_stream(frame_type, stream_id)
        if max_stream_data > stream.max_stream_data_remote:
            self._logger.debug(
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
        max_streams = buf.pull_uint_var()
        if max_streams > self._remote_max_streams_bidi:
            self._logger.debug("Remote max_streams_bidi raised to %d", max_streams)
            self._remote_max_streams_bidi = max_streams
            self._unblock_streams(is_unidirectional=False)

    def _handle_max_streams_uni_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a MAX_STREAMS_UNI frame.

        This raises number of unidirectional streams we can initiate to the peer.
        """
        max_streams = buf.pull_uint_var()
        if max_streams > self._remote_max_streams_uni:
            self._logger.debug("Remote max_streams_uni raised to %d", max_streams)
            self._remote_max_streams_uni = max_streams
            self._unblock_streams(is_unidirectional=True)

    def _handle_new_connection_id_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a NEW_CONNECTION_ID frame.
        """
        sequence_number, retire_prior_to, cid, stateless_reset_token = pull_new_connection_id_frame(
            buf
        )
        self._logger.debug(
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
        data = buf.pull_bytes(8)
        context.network_path.remote_challenge = data

    def _handle_path_response_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a PATH_RESPONSE frame.
        """
        data = buf.pull_bytes(8)
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
        stream_id = buf.pull_uint_var()
        error_code = buf.pull_uint_var()
        final_size = buf.pull_uint_var()

        # check stream direction
        self._assert_stream_can_receive(frame_type, stream_id)

        self._logger.info(
            "Stream %d reset by peer (error code %d, final size %d)",
            stream_id,
            error_code,
            final_size,
        )
        # stream = self._get_or_create_stream(frame_type, stream_id)
        self._events.append(events.StreamReset(stream_id=stream_id))

    def _handle_retire_connection_id_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a RETIRE_CONNECTION_ID frame.
        """
        sequence_number = buf.pull_uint_var()

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
                self._events.append(
                    events.ConnectionIdRetired(connection_id=connection_id.cid)
                )
                break

        # issue a new connection ID
        self._replenish_connection_ids()

    def _handle_stop_sending_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STOP_SENDING frame.
        """
        stream_id = buf.pull_uint_var()
        buf.pull_uint_var()  # application error code

        # check stream direction
        self._assert_stream_can_send(frame_type, stream_id)

        self._get_or_create_stream(frame_type, stream_id)

    def _handle_stream_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAM frame.
        """
        stream_id = buf.pull_uint_var()
        if frame_type & 4:
            offset = buf.pull_uint_var()
        else:
            offset = 0
        if frame_type & 2:
            length = buf.pull_uint_var()
        else:
            length = buf.capacity - buf.tell()
        frame = QuicStreamFrame(
            offset=offset, data=buf.pull_bytes(length), fin=bool(frame_type & 1)
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
        stream_id = buf.pull_uint_var()
        buf.pull_uint_var()  # limit

        # check stream direction
        self._assert_stream_can_receive(frame_type, stream_id)

        self._get_or_create_stream(frame_type, stream_id)

    def _handle_streams_blocked_frame(
        self, context: QuicReceiveContext, frame_type: int, buf: Buffer
    ) -> None:
        """
        Handle a STREAMS_BLOCKED frame.
        """
        buf.pull_uint_var()  # limit

    def _on_ack_delivery(
        self, delivery: QuicDeliveryState, space: QuicPacketSpace, highest_acked: int
    ) -> None:
        """
        Callback when an ACK frame is acknowledged or lost.
        """
        if delivery == QuicDeliveryState.ACKED:
            space.ack_queue.subtract(0, highest_acked + 1)

    def _on_max_data_delivery(self, delivery: QuicDeliveryState) -> None:
        """
        Callback when a MAX_DATA frame is acknowledged or lost.
        """
        if delivery != QuicDeliveryState.ACKED:
            self._local_max_data_sent = 0

    def _on_max_stream_data_delivery(
        self, delivery: QuicDeliveryState, stream: QuicStream
    ) -> None:
        """
        Callback when a MAX_STREAM_DATA frame is acknowledged or lost.
        """
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

    def _on_ping_delivery(
        self, delivery: QuicDeliveryState, uids: Sequence[int]
    ) -> None:
        """
        Callback when a PING frame is acknowledged or lost.
        """
        if delivery == QuicDeliveryState.ACKED:
            self._logger.info("Received PING response")
            for uid in uids:
                self._events.append(events.PingAcknowledged(uid=uid))
        else:
            self._ping_pending.extend(uids)

    def _on_retire_connection_id_delivery(
        self, delivery: QuicDeliveryState, sequence_number: int
    ) -> None:
        """
        Callback when a RETIRE_CONNECTION_ID frame is acknowledged or lost.
        """
        if delivery != QuicDeliveryState.ACKED:
            self._retire_connection_ids.append(sequence_number)

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
            frame_type = buf.pull_uint_var()

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
        while len(self._host_cids) < min(8, self._remote_active_connection_id_limit):
            self._host_cids.append(
                QuicConnectionId(
                    cid=os.urandom(8),
                    sequence_number=self._host_cid_seq,
                    stateless_reset_token=os.urandom(16),
                )
            )
            self._host_cid_seq += 1

    def _push_crypto_data(self) -> None:
        for epoch, buf in self._crypto_buffers.items():
            self._crypto_streams[epoch].write(buf.data)
            buf.seek(0)

    def _send_probe(self) -> None:
        self._probe_pending = True

    def _parse_transport_parameters(
        self, data: bytes, from_session_ticket: bool = False
    ) -> None:
        quic_transport_parameters = pull_quic_transport_parameters(Buffer(data=data))

        # validate remote parameters
        if (
            self._is_client
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
        if quic_transport_parameters.active_connection_id_limit is not None:
            self._remote_active_connection_id_limit = (
                quic_transport_parameters.active_connection_id_limit
            )
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

    def _serialize_transport_parameters(self) -> bytes:
        quic_transport_parameters = QuicTransportParameters(
            active_connection_id_limit=self._local_active_connection_id_limit,
            idle_timeout=int(self._configuration.idle_timeout * 1000),
            initial_max_data=self._local_max_data,
            initial_max_stream_data_bidi_local=self._local_max_stream_data_bidi_local,
            initial_max_stream_data_bidi_remote=self._local_max_stream_data_bidi_remote,
            initial_max_stream_data_uni=self._local_max_stream_data_uni,
            initial_max_streams_bidi=self._local_max_streams_bidi,
            initial_max_streams_uni=self._local_max_streams_uni,
            ack_delay_exponent=10,
        )
        if not self._is_client:
            quic_transport_parameters.original_connection_id = (
                self._original_connection_id
            )

        buf = Buffer(capacity=512)
        push_quic_transport_parameters(buf, quic_transport_parameters)
        return buf.data

    def _set_state(self, state: QuicConnectionState) -> None:
        self._logger.debug("%s -> %s", self._state, state)
        self._state = state

    def _stream_can_receive(self, stream_id: int) -> bool:
        return stream_is_client_initiated(
            stream_id
        ) != self._is_client or not stream_is_unidirectional(stream_id)

    def _stream_can_send(self, stream_id: int) -> bool:
        return stream_is_client_initiated(
            stream_id
        ) == self._is_client or not stream_is_unidirectional(stream_id)

    def _unblock_streams(self, is_unidirectional: bool) -> None:
        if is_unidirectional:
            max_stream_data_remote = self._remote_max_stream_data_uni
            max_streams = self._remote_max_streams_uni
            streams_blocked = self._streams_blocked_uni
        else:
            max_stream_data_remote = self._remote_max_stream_data_bidi_remote
            max_streams = self._remote_max_streams_bidi
            streams_blocked = self._streams_blocked_bidi

        while streams_blocked and streams_blocked[0].stream_id // 4 < max_streams:
            stream = streams_blocked.pop(0)
            stream.is_blocked = False
            stream.max_stream_data_remote = max_stream_data_remote

        if not self._streams_blocked_bidi and not self._streams_blocked_uni:
            self._streams_blocked_pending = False

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
        secrets_log_file = self._configuration.secrets_log_file
        if secrets_log_file is not None:
            label_row = self._is_client == (direction == tls.Direction.DECRYPT)
            label = SECRETS_LABELS[label_row][epoch.value]
            secrets_log_file.write(
                "%s %s %s\n" % (label, self.tls.client_random.hex(), secret.hex())
            )
            secrets_log_file.flush()

        crypto = self._cryptos[epoch]
        if direction == tls.Direction.ENCRYPT:
            crypto.send.setup(cipher_suite, secret)
        else:
            crypto.recv.setup(cipher_suite, secret)

    def _write_application(
        self, builder: QuicPacketBuilder, network_path: QuicNetworkPath, now: float
    ) -> None:
        crypto_stream: Optional[QuicStream] = None
        if self._cryptos[tls.Epoch.ONE_RTT].send.is_valid():
            crypto = self._cryptos[tls.Epoch.ONE_RTT]
            crypto_stream = self._crypto_streams[tls.Epoch.ONE_RTT]
            packet_type = PACKET_TYPE_ONE_RTT
        elif self._cryptos[tls.Epoch.ZERO_RTT].send.is_valid():
            crypto = self._cryptos[tls.Epoch.ZERO_RTT]
            packet_type = PACKET_TYPE_ZERO_RTT
        else:
            return
        space = self._spaces[tls.Epoch.ONE_RTT]

        buf = builder.buffer

        while True:
            # write header
            builder.start_packet(packet_type, crypto)

            if self._handshake_complete:
                # ACK
                if space.ack_at is not None and space.ack_at <= now:
                    builder.start_frame(
                        QuicFrameType.ACK,
                        self._on_ack_delivery,
                        (space, space.largest_received_packet),
                    )
                    push_ack_frame(buf, space.ack_queue, 0)
                    space.ack_at = None

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
                    buf.push_bytes(network_path.local_challenge)

                # PATH RESPONSE
                if network_path.remote_challenge is not None:
                    builder.start_frame(QuicFrameType.PATH_RESPONSE)
                    buf.push_bytes(network_path.remote_challenge)
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
                            0,  # FIXME: retire_prior_to
                            connection_id.cid,
                            connection_id.stateless_reset_token,
                        )
                        connection_id.was_sent = True
                        self._events.append(
                            events.ConnectionIdIssued(connection_id=connection_id.cid)
                        )

                # RETIRE_CONNECTION_ID
                while self._retire_connection_ids:
                    sequence_number = self._retire_connection_ids.pop(0)
                    builder.start_frame(
                        QuicFrameType.RETIRE_CONNECTION_ID,
                        self._on_retire_connection_id_delivery,
                        (sequence_number,),
                    )
                    buf.push_uint_var(sequence_number)

                # STREAMS_BLOCKED
                if self._streams_blocked_pending:
                    if self._streams_blocked_bidi:
                        builder.start_frame(QuicFrameType.STREAMS_BLOCKED_BIDI)
                        buf.push_uint_var(self._remote_max_streams_bidi)
                    if self._streams_blocked_uni:
                        builder.start_frame(QuicFrameType.STREAMS_BLOCKED_UNI)
                        buf.push_uint_var(self._remote_max_streams_uni)
                    self._streams_blocked_pending = False

                # connection-level limits
                self._write_connection_limits(builder=builder, space=space)

            # stream-level limits
            for stream in self._streams.values():
                self._write_stream_limits(builder=builder, space=space, stream=stream)

            # PING (user-request)
            if self._ping_pending:
                self._logger.info("Sending PING in packet %d", builder.packet_number)
                builder.start_frame(
                    QuicFrameType.PING,
                    self._on_ping_delivery,
                    (tuple(self._ping_pending),),
                )
                self._ping_pending.clear()

            # PING (probe)
            if self._probe_pending:
                self._logger.info("Sending probe")
                builder.start_frame(QuicFrameType.PING)
                self._probe_pending = False

            # CRYPTO
            if crypto_stream is not None and not crypto_stream.send_buffer_is_empty:
                write_crypto_frame(builder=builder, space=space, stream=crypto_stream)

            for stream in self._streams.values():
                # STREAM
                if not stream.is_blocked and not stream.send_buffer_is_empty:
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
        crypto = self._cryptos[epoch]
        if not crypto.send.is_valid():
            return

        buf = builder.buffer
        crypto_stream = self._crypto_streams[epoch]
        space = self._spaces[epoch]

        while True:
            if epoch == tls.Epoch.INITIAL:
                packet_type = PACKET_TYPE_INITIAL
            else:
                packet_type = PACKET_TYPE_HANDSHAKE
            builder.start_packet(packet_type, crypto)

            # ACK
            if space.ack_at is not None:
                builder.start_frame(QuicFrameType.ACK)
                push_ack_frame(buf, space.ack_queue, 0)
                space.ack_at = None

            # CRYPTO
            if not crypto_stream.send_buffer_is_empty:
                write_crypto_frame(builder=builder, space=space, stream=crypto_stream)

            # PADDING (anti-deadlock packet)
            if self._probe_pending and self._is_client and epoch == tls.Epoch.HANDSHAKE:
                buf.push_bytes(bytes(builder.remaining_space))
                self._probe_pending = False

            if not builder.end_packet():
                break

    def _write_connection_limits(
        self, builder: QuicPacketBuilder, space: QuicPacketSpace
    ) -> None:
        # raise MAX_DATA if needed
        if self._local_max_data_used > self._local_max_data * 0.75:
            self._local_max_data *= 2
            self._logger.debug("Local max_data raised to %d", self._local_max_data)
        if self._local_max_data_sent != self._local_max_data:
            builder.start_frame(QuicFrameType.MAX_DATA, self._on_max_data_delivery)
            builder.buffer.push_uint_var(self._local_max_data)
            self._local_max_data_sent = self._local_max_data

    def _write_stream_limits(
        self, builder: QuicPacketBuilder, space: QuicPacketSpace, stream: QuicStream
    ) -> None:
        # raise MAX_STREAM_DATA if needed
        if stream._recv_highest > stream.max_stream_data_local * 0.75:
            stream.max_stream_data_local *= 2
            self._logger.debug(
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
            builder.buffer.push_uint_var(stream.stream_id)
            builder.buffer.push_uint_var(stream.max_stream_data_local)
            stream.max_stream_data_local_sent = stream.max_stream_data_local
