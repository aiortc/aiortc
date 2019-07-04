import logging
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

from pylsqpack import Decoder, Encoder

import aioquic.events
from aioquic.buffer import Buffer, BufferReadError
from aioquic.connection import QuicConnection
from aioquic.h3.events import (
    DataReceived,
    Event,
    Headers,
    RequestReceived,
    ResponseReceived,
)

logger = logging.getLogger("http3")


class FrameType(IntEnum):
    DATA = 0
    HEADERS = 1
    PRIORITY = 2
    CANCEL_PUSH = 3
    SETTINGS = 4
    PUSH_PROMISE = 5
    GOAWAY = 6
    MAX_PUSH_ID = 7
    DUPLICATE_PUSH = 8


class Setting(IntEnum):
    QPACK_MAX_TABLE_CAPACITY = 1
    SETTINGS_MAX_HEADER_LIST_SIZE = 6
    QPACK_BLOCKED_STREAMS = 7
    SETTINGS_NUM_PLACEHOLDERS = 9


class StreamType(IntEnum):
    CONTROL = 0
    PUSH = 1
    QPACK_ENCODER = 2
    QPACK_DECODER = 3


def parse_settings(data: bytes) -> Dict[int, int]:
    buf = Buffer(data=data)
    settings = []
    while not buf.eof():
        setting = buf.pull_uint_var()
        value = buf.pull_uint_var()
        settings.append((setting, value))
    return dict(settings)


class H3Connection:
    """
    A low-level HTTP/3 connection object.

    :param quic: A :class:`~aioquic.connection.QuicConnection` instance.
    """

    def __init__(self, quic: QuicConnection):
        self._handshake_completed = False
        self._is_client = quic.configuration.is_client
        self._quic = quic
        self._decoder = Decoder(0x100, 0x10)
        self._encoder = Encoder()
        self._pending: List[Tuple[int, bytes, bool]] = []
        self._stream_buffers: Dict[int, bytes] = {}
        self._stream_types: Dict[int, int] = {}

        self._peer_control_stream_id: Optional[int] = None
        self._peer_decoder_stream_id: Optional[int] = None
        self._peer_encoder_stream_id: Optional[int] = None

    def receive_datagram(self, data: bytes, addr: Any, now: float) -> List[Event]:
        """
        Handle an incoming datagram and return events.
        """
        self._quic.receive_datagram(data, addr, now=now)
        return self._update()

    def send_data(self, stream_id: int, data: bytes, end_stream: bool) -> None:
        """
        Send data on the given stream.

        To retrieve datagram which need to be sent over the network call the QUIC
        connection's :meth:`~aioquic.connection.QuicConnection.datagrams_to_send`
        method.
        """
        buf = Buffer(capacity=len(data) + 16)
        buf.push_uint_var(FrameType.DATA)
        buf.push_uint_var(len(data))
        buf.push_bytes(data)

        self._send_stream_data(stream_id, buf.data, end_stream)

    def send_headers(self, stream_id: int, headers: Headers) -> None:
        """
        Send headers on the given stream.

        To retrieve datagram which need to be sent over the network call the QUIC
        connection's :meth:`~aioquic.connection.QuicConnection.datagrams_to_send`
        method.
        """
        control, header = self._encoder.encode(stream_id, 0, headers)

        buf = Buffer(capacity=len(header) + 16)
        buf.push_uint_var(FrameType.HEADERS)
        buf.push_uint_var(len(header))
        buf.push_bytes(header)

        self._send_stream_data(stream_id, buf.data, False)

    def _receive_stream_data(
        self, stream_id: int, data: bytes, stream_ended: bool
    ) -> List[Event]:
        http_events: List[Event] = []

        if stream_id in self._stream_buffers:
            self._stream_buffers[stream_id] += data
        else:
            self._stream_buffers[stream_id] = data
        consumed = 0

        buf = Buffer(data=self._stream_buffers[stream_id])
        while not buf.eof():
            # fetch stream type for unidirectional streams
            if (stream_id % 4) == 3 and stream_id not in self._stream_types:
                try:
                    stream_type = buf.pull_uint_var()
                except BufferReadError:
                    break
                if stream_type == StreamType.CONTROL:
                    assert self._peer_control_stream_id is None
                    self._peer_control_stream_id = stream_id
                elif stream_type == StreamType.QPACK_DECODER:
                    assert self._peer_decoder_stream_id is None
                    self._peer_decoder_stream_id = stream_id
                elif stream_type == StreamType.QPACK_ENCODER:
                    assert self._peer_encoder_stream_id is None
                    self._peer_encoder_stream_id = stream_id
                self._stream_types[stream_id] = stream_type

            # fetch next frame
            try:
                frame_type = buf.pull_uint_var()
                frame_length = buf.pull_uint_var()
                frame_data = buf.pull_bytes(frame_length)
            except BufferReadError:
                break
            consumed = buf.tell()

            if (stream_id % 4) == 0:
                # bidirectional streams carry requests and responses
                if frame_type == FrameType.DATA:
                    http_events.append(
                        DataReceived(
                            data=frame_data,
                            stream_id=stream_id,
                            stream_ended=stream_ended and buf.eof(),
                        )
                    )
                elif frame_type == FrameType.HEADERS:
                    control, headers = self._decoder.feed_header(stream_id, frame_data)
                    cls = ResponseReceived if self._is_client else RequestReceived
                    http_events.append(
                        cls(
                            headers=headers,
                            stream_id=stream_id,
                            stream_ended=stream_ended and buf.eof(),
                        )
                    )
                else:
                    logger.info(
                        "Unhandled frame type %d on stream %d", frame_type, stream_id
                    )
            elif stream_id == self._peer_control_stream_id:
                # unidirectional control stream
                if frame_type == FrameType.SETTINGS:
                    settings = parse_settings(frame_data)
                    self._encoder.apply_settings(
                        max_table_capacity=settings.get(
                            Setting.QPACK_MAX_TABLE_CAPACITY, 0
                        ),
                        blocked_streams=settings.get(Setting.QPACK_BLOCKED_STREAMS, 0),
                    )

        # remove processed data from buffer
        self._stream_buffers[stream_id] = self._stream_buffers[stream_id][consumed:]

        return http_events

    def _send_stream_data(self, stream_id: int, data: bytes, end_stream: bool) -> None:
        if self._handshake_completed:
            self._quic.send_stream_data(stream_id, data, end_stream)
        else:
            self._pending.append((stream_id, data, end_stream))

    def _update(self) -> List[Event]:
        http_events: List[Event] = []

        # process QUIC events
        event = self._quic.next_event()
        while event is not None:
            if isinstance(event, aioquic.events.HandshakeCompleted):
                self._handshake_completed = True
                for args in self._pending:
                    self._quic.send_stream_data(*args)
                self._pending.clear()
            elif isinstance(event, aioquic.events.StreamDataReceived):
                http_events.extend(
                    self._receive_stream_data(
                        event.stream_id, event.data, event.end_stream
                    )
                )

            event = self._quic.next_event()

        return http_events
