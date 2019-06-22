import argparse
import logging
import socket
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from pylsqpack import Decoder, Encoder

import aioquic.events
from aioquic.buffer import Buffer, BufferReadError
from aioquic.configuration import QuicConfiguration
from aioquic.connection import QuicConnection

logger = logging.getLogger("http3")


Headers = List[Tuple[bytes, bytes]]


class Event:
    pass


@dataclass
class DataReceived(Event):
    data: bytes
    stream_id: int
    stream_ended: bool


@dataclass
class ResponseReceived(Event):
    headers: Headers
    stream_id: int
    stream_ended: bool


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


class StreamType(IntEnum):
    CONTROL = 0
    PUSH = 1
    QPACK_ENCODER = 2
    QPACK_DECODER = 3


class H3Connection:
    def __init__(self, configuration: QuicConfiguration):
        self._quic = QuicConnection(configuration=configuration)
        self._decoder = Decoder(0x100, 0x10)
        self._encoder = Encoder()
        self._pending: List[Tuple[int, bytes, bool]] = []
        self._stream_buffers: Dict[int, bytes] = {}

        self._local_control_stream_id = 2
        self._peer_control_stream_id: Optional[int] = None
        self._peer_decoder_stream_id: Optional[int] = None
        self._peer_encoder_stream_id: Optional[int] = None

    def datagrams_to_send(self) -> List[Tuple[bytes, Any]]:
        return self._quic.datagrams_to_send(now=time.time())

    def initiate_connection(self, addr: Any) -> None:
        self._quic.connect(addr, now=time.time())

    def receive_datagram(self, data: bytes, addr: Any) -> List[Event]:
        self._quic.receive_datagram(data, addr, now=time.time())
        return self._update()

    def send_data(self, stream_id: int, data: bytes, end_stream: bool):
        buf = Buffer(capacity=len(data) + 16)
        buf.push_uint_var(FrameType.DATA)
        buf.push_uint_var(len(data))
        buf.push_bytes(data)

        self._pending.append((stream_id, buf.data, end_stream))

    def send_headers(self, stream_id: int, headers: Headers):
        control, header = self._encoder.encode(stream_id, 0, headers)

        buf = Buffer(capacity=len(header) + 16)
        buf.push_uint_var(FrameType.HEADERS)
        buf.push_uint_var(len(header))
        buf.push_bytes(header)

        self._pending.append((stream_id, buf.data, False))

    def _receive_stream_data(
        self, stream_id: int, data: bytes, stream_ended: bool
    ) -> List[Event]:
        http_events: List[Event] = []

        if stream_id in self._stream_buffers:
            self._stream_buffers[stream_id] += data
            stream_is_new = False
        else:
            self._stream_buffers[stream_id] = data
            stream_is_new = True
        consumed = 0

        buf = Buffer(data=self._stream_buffers[stream_id])
        while not buf.eof():
            # fetch stream type for unidirectional streams
            if stream_is_new and (stream_id % 4) == 3:
                stream_type = buf.pull_uint_var()
                if stream_type == StreamType.CONTROL:
                    assert self._peer_control_stream_id is None
                    self._peer_control_stream_id = stream_id
                elif stream_type == StreamType.QPACK_DECODER:
                    assert self._peer_decoder_stream_id is None
                    self._peer_decoder_stream_id = stream_id
                elif stream_type == StreamType.QPACK_ENCODER:
                    assert self._peer_encoder_stream_id is None
                    self._peer_encoder_stream_id = stream_id

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
                    http_events.append(
                        ResponseReceived(
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
                    pass

        # remove processed data from buffer
        self._stream_buffers[stream_id] = self._stream_buffers[stream_id][consumed:]

        return http_events

    def _update(self) -> List[Event]:
        http_events: List[Event] = []

        # process QUIC events
        event = self._quic.next_event()
        while event is not None:
            if isinstance(event, aioquic.events.HandshakeCompleted):
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


def run(url: str) -> None:
    # parse URL
    parsed = urlparse(url)
    assert parsed.scheme == "https", "Only HTTPS URLs are supported."
    if ":" in parsed.netloc:
        server_name, port_str = parsed.netloc.split(":")
        port = int(port_str)
    else:
        server_name = parsed.netloc
        port = 443
    server_addr = (socket.gethostbyname(server_name), port)
    stream_id = 0

    conn = H3Connection(
        QuicConfiguration(
            alpn_protocols=["h3-20"],
            is_client=True,
            secrets_log_file=open("/tmp/ssl.log", "w"),
            server_name=server_name,
        )
    )
    conn.initiate_connection(server_addr)
    conn.send_headers(
        stream_id=stream_id,
        headers=[
            (b":method", b"GET"),
            (b":scheme", b"https"),
            (b":path", parsed.path.encode("utf8")),
            (b"host", server_name.encode("utf8")),
        ],
    )
    conn.send_data(stream_id=stream_id, data=b"", end_stream=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for data, addr in conn.datagrams_to_send():
        sock.sendto(data, addr)

    stream_ended = False
    while not stream_ended:
        data, addr = sock.recvfrom(2048)
        for event in conn.receive_datagram(data, addr):
            print(event)
            if isinstance(event, (DataReceived, ResponseReceived)):
                stream_ended = event.stream_ended

        for data, addr in conn.datagrams_to_send():
            sock.sendto(data, addr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP/3 client")
    parser.add_argument("url", type=str, help="the server's host name or address")
    args = parser.parse_args()
    run(args.url)
