from typing import List, Tuple

import aioquic.events
from aioquic.connection import QuicConnection
from aioquic.h3.events import RequestReceived, ResponseReceived


class H0Connection:
    """
    An HTTP/0.9 connection object.
    """

    def __init__(self, quic: QuicConnection):
        self._is_client = quic.configuration.is_client
        self._quic = quic

    def handle_event(self, event: aioquic.events.Event):
        http_events = []

        if (
            isinstance(event, aioquic.events.StreamDataReceived)
            and (event.stream_id % 4) == 0
        ):
            method, path = event.data.rstrip().split(b" ", 1)
            cls = ResponseReceived if self._is_client else RequestReceived
            http_events.append(
                cls(
                    headers=[(b":method", method), (b":path", path)],
                    stream_ended=event.end_stream,
                    stream_id=event.stream_id,
                )
            )

        return http_events

    def send_data(self, stream_id: int, data: bytes, end_stream: bool) -> None:
        self._quic.send_stream_data(stream_id, data, end_stream)

    def send_headers(self, stream_id: int, headers: List[Tuple[bytes, bytes]]) -> None:
        # HTTP/0.9 has no concept of headers.
        pass
