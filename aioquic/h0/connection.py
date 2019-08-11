from typing import Dict, List, Tuple

from aioquic.h3.events import DataReceived, HttpEvent, RequestReceived, ResponseReceived
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import QuicEvent, StreamDataReceived


class H0Connection:
    """
    An HTTP/0.9 connection object.
    """

    def __init__(self, quic: QuicConnection):
        self._headers_received: Dict[int, bool] = {}
        self._is_client = quic.configuration.is_client
        self._quic = quic

    def handle_event(self, event: QuicEvent) -> List[HttpEvent]:
        http_events: List[HttpEvent] = []

        if isinstance(event, StreamDataReceived) and (event.stream_id % 4) == 0:
            data = event.data
            if not self._headers_received.get(event.stream_id, False):
                if self._is_client:
                    http_events.append(
                        ResponseReceived(
                            headers=[], stream_ended=False, stream_id=event.stream_id
                        )
                    )
                else:
                    method, path = data.rstrip().split(b" ", 1)
                    http_events.append(
                        RequestReceived(
                            headers=[(b":method", method), (b":path", path)],
                            stream_ended=False,
                            stream_id=event.stream_id,
                        )
                    )
                    data = b""
                self._headers_received[event.stream_id] = True

            http_events.append(
                DataReceived(
                    data=data, stream_ended=event.end_stream, stream_id=event.stream_id
                )
            )

        return http_events

    def send_data(self, stream_id: int, data: bytes, end_stream: bool) -> None:
        self._quic.send_stream_data(stream_id, data, end_stream)

    def send_headers(self, stream_id: int, headers: List[Tuple[bytes, bytes]]) -> None:
        if self._is_client:
            headers_dict = dict(headers)
            self._quic.send_stream_data(
                stream_id,
                headers_dict[b":method"] + b" " + headers_dict[b":path"] + b"\r\n",
                False,
            )
