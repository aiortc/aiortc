import logging
from enum import IntEnum
from typing import Dict, List, Optional, Set

from pylsqpack import Decoder, Encoder, StreamBlocked

from aioquic.buffer import Buffer, BufferReadError, encode_uint_var
from aioquic.h3.events import (
    DataReceived,
    Headers,
    HttpEvent,
    RequestReceived,
    ResponseReceived,
)
from aioquic.quic.connection import QuicConnection, stream_is_unidirectional
from aioquic.quic.events import QuicEvent, StreamDataReceived

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


def encode_frame(frame_type: int, frame_data: bytes) -> bytes:
    frame_length = len(frame_data)
    buf = Buffer(capacity=frame_length + 16)
    buf.push_uint_var(frame_type)
    buf.push_uint_var(frame_length)
    buf.push_bytes(frame_data)
    return buf.data


def encode_settings(settings: Dict[int, int]) -> bytes:
    buf = Buffer(capacity=1024)
    for setting, value in settings.items():
        buf.push_uint_var(setting)
        buf.push_uint_var(value)
    return buf.data


def parse_settings(data: bytes) -> Dict[int, int]:
    buf = Buffer(data=data)
    settings = []
    while not buf.eof():
        setting = buf.pull_uint_var()
        value = buf.pull_uint_var()
        settings.append((setting, value))
    return dict(settings)


class H3Stream:
    def __init__(self) -> None:
        self.blocked = False
        self.buffer = b""
        self.ended = False
        self.frame_size: Optional[int] = None
        self.frame_type: Optional[int] = None
        self.stream_type: Optional[int] = None


class H3Connection:
    """
    A low-level HTTP/3 connection object.

    :param quic: A :class:`~aioquic.connection.QuicConnection` instance.
    """

    def __init__(self, quic: QuicConnection):
        self._max_table_capacity = 0x100
        self._blocked_streams = 0x10

        self._is_client = quic.configuration.is_client
        self._quic = quic
        self._decoder = Decoder(self._max_table_capacity, self._blocked_streams)
        self._encoder = Encoder()
        self._stream: Dict[int, H3Stream] = {}

        self._local_control_stream_id: Optional[int] = None
        self._local_decoder_stream_id: Optional[int] = None
        self._local_encoder_stream_id: Optional[int] = None

        self._peer_control_stream_id: Optional[int] = None
        self._peer_decoder_stream_id: Optional[int] = None
        self._peer_encoder_stream_id: Optional[int] = None

        self._init_connection()

    def handle_event(self, event: QuicEvent) -> List[HttpEvent]:
        """
        Handle a QUIC event and return a list of HTTP events.

        :param event: The QUIC event to handle.
        """
        if isinstance(event, StreamDataReceived):
            stream_id = event.stream_id
            if stream_id not in self._stream:
                self._stream[stream_id] = H3Stream()
            if stream_id % 4 == 0:
                return self._receive_stream_data_bidi(
                    stream_id, event.data, event.end_stream
                )
            elif stream_is_unidirectional(stream_id):
                return self._receive_stream_data_uni(stream_id, event.data)
        return []

    def send_data(self, stream_id: int, data: bytes, end_stream: bool) -> None:
        """
        Send data on the given stream.

        To retrieve datagram which need to be sent over the network call the QUIC
        connection's :meth:`~aioquic.connection.QuicConnection.datagrams_to_send`
        method.

        :param stream_id: The stream ID on which to send the data.
        :param data: The data to send.
        :param end_stream: Whether to end the stream.
        """
        self._quic.send_stream_data(
            stream_id, encode_frame(FrameType.DATA, data), end_stream
        )

    def send_headers(self, stream_id: int, headers: Headers) -> None:
        """
        Send headers on the given stream.

        To retrieve datagram which need to be sent over the network call the QUIC
        connection's :meth:`~aioquic.connection.QuicConnection.datagrams_to_send`
        method.

        :param stream_id: The stream ID on which to send the headers.
        :param headers: The HTTP headers to send.
        """
        encoder, header = self._encoder.encode(stream_id, 0, headers)
        self._quic.send_stream_data(self._local_encoder_stream_id, encoder)
        self._quic.send_stream_data(stream_id, encode_frame(FrameType.HEADERS, header))

    def _create_uni_stream(self, stream_type: int) -> int:
        """
        Create an unidirectional stream of the given type.
        """
        stream_id = self._quic.get_next_available_stream_id(is_unidirectional=True)
        self._quic.send_stream_data(stream_id, encode_uint_var(stream_type))
        return stream_id

    def _init_connection(self) -> None:
        # send our settings
        self._local_control_stream_id = self._create_uni_stream(StreamType.CONTROL)
        self._quic.send_stream_data(
            self._local_control_stream_id,
            encode_frame(
                FrameType.SETTINGS,
                encode_settings(
                    {
                        Setting.QPACK_MAX_TABLE_CAPACITY: self._max_table_capacity,
                        Setting.QPACK_BLOCKED_STREAMS: self._blocked_streams,
                    }
                ),
            ),
        )

        # create encoder and decoder streams
        self._local_encoder_stream_id = self._create_uni_stream(
            StreamType.QPACK_ENCODER
        )
        self._local_decoder_stream_id = self._create_uni_stream(
            StreamType.QPACK_DECODER
        )

    def _receive_stream_data_bidi(
        self, stream_id: int, data: bytes, stream_ended: bool
    ) -> List[HttpEvent]:
        """
        Client-initiated bidirectional streams carry requests and responses.
        """
        http_events: List[HttpEvent] = []

        stream = self._stream[stream_id]
        stream.buffer += data
        if stream_ended:
            stream.ended = True
        if stream.blocked:
            return http_events

        # shortcut DATA frame bits
        if (
            stream.frame_size is not None
            and stream.frame_type == FrameType.DATA
            and len(stream.buffer) < stream.frame_size
        ):
            http_events.append(
                DataReceived(
                    data=stream.buffer, stream_id=stream_id, stream_ended=False
                )
            )
            stream.frame_size -= len(stream.buffer)
            stream.buffer = b""
            return http_events

        # some peers (e.g. f5) end the stream with no data
        if stream_ended and not stream.buffer:
            http_events.append(
                DataReceived(data=b"", stream_id=stream_id, stream_ended=True)
            )
            return http_events

        buf = Buffer(data=stream.buffer)
        consumed = 0

        while not buf.eof():
            # fetch next frame header
            if stream.frame_size is None:
                try:
                    stream.frame_type = buf.pull_uint_var()
                    stream.frame_size = buf.pull_uint_var()
                except BufferReadError:
                    break
                consumed = buf.tell()

            # check how much data is available
            chunk_size = min(stream.frame_size, buf.capacity - consumed)
            if (
                stream.frame_type == FrameType.HEADERS
                and chunk_size < stream.frame_size
            ):
                break

            # read available data
            frame_data = buf.pull_bytes(chunk_size)
            consumed = buf.tell()

            # detect end of frame
            stream.frame_size -= chunk_size
            if not stream.frame_size:
                stream.frame_size = None

            if stream.frame_type == FrameType.DATA and (stream_ended or frame_data):
                http_events.append(
                    DataReceived(
                        data=frame_data,
                        stream_id=stream_id,
                        stream_ended=stream_ended and buf.eof(),
                    )
                )
            elif stream.frame_type == FrameType.HEADERS:
                try:
                    decoder, headers = self._decoder.feed_header(stream_id, frame_data)
                except StreamBlocked:
                    stream.blocked = True
                    break
                self._quic.send_stream_data(self._local_decoder_stream_id, decoder)
                cls = ResponseReceived if self._is_client else RequestReceived
                http_events.append(
                    cls(
                        headers=headers,
                        stream_id=stream_id,
                        stream_ended=stream_ended and buf.eof(),
                    )
                )

        # remove processed data from buffer
        stream.buffer = stream.buffer[consumed:]

        return http_events

    def _receive_stream_data_uni(self, stream_id: int, data: bytes) -> List[HttpEvent]:
        http_events: List[HttpEvent] = []

        stream = self._stream[stream_id]
        stream.buffer += data

        buf = Buffer(data=stream.buffer)
        consumed = 0
        unblocked_streams: Set[int] = set()

        while not buf.eof():
            # fetch stream type for unidirectional streams
            if stream.stream_type is None:
                try:
                    stream.stream_type = buf.pull_uint_var()
                except BufferReadError:
                    break
                consumed = buf.tell()

                if stream.stream_type == StreamType.CONTROL:
                    assert self._peer_control_stream_id is None
                    self._peer_control_stream_id = stream_id
                elif stream.stream_type == StreamType.QPACK_DECODER:
                    assert self._peer_decoder_stream_id is None
                    self._peer_decoder_stream_id = stream_id
                elif stream.stream_type == StreamType.QPACK_ENCODER:
                    assert self._peer_encoder_stream_id is None
                    self._peer_encoder_stream_id = stream_id

            if stream_id == self._peer_control_stream_id:
                # fetch next frame
                try:
                    frame_type = buf.pull_uint_var()
                    frame_length = buf.pull_uint_var()
                    frame_data = buf.pull_bytes(frame_length)
                except BufferReadError:
                    break
                consumed = buf.tell()

                # unidirectional control stream
                if frame_type == FrameType.SETTINGS:
                    settings = parse_settings(frame_data)
                    encoder = self._encoder.apply_settings(
                        max_table_capacity=settings.get(
                            Setting.QPACK_MAX_TABLE_CAPACITY, 0
                        ),
                        blocked_streams=settings.get(Setting.QPACK_BLOCKED_STREAMS, 0),
                    )
                    self._quic.send_stream_data(self._local_encoder_stream_id, encoder)
            else:
                # fetch unframed data
                data = buf.pull_bytes(buf.capacity - buf.tell())
                consumed = buf.tell()

                if stream_id == self._peer_decoder_stream_id:
                    self._encoder.feed_decoder(data)

                elif stream_id == self._peer_encoder_stream_id:
                    unblocked_streams.update(self._decoder.feed_encoder(data))

        # remove processed data from buffer
        stream.buffer = stream.buffer[consumed:]

        # process unblocked streams
        for stream_id in unblocked_streams:
            stream = self._stream[stream_id]
            decoder, headers = self._decoder.resume_header(stream_id)
            stream.blocked = False
            cls = ResponseReceived if self._is_client else RequestReceived
            http_events.append(
                cls(
                    headers=headers,
                    stream_id=stream_id,
                    stream_ended=stream.ended and not stream.buffer,
                )
            )
            http_events.extend(
                self._receive_stream_data_bidi(stream_id, b"", stream.ended)
            )

        return http_events
