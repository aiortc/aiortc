from dataclasses import dataclass
from typing import List, Tuple

Headers = List[Tuple[bytes, bytes]]


class HttpEvent:
    """
    Base class for HTTP/3 events.
    """

    pass


@dataclass
class DataReceived(HttpEvent):
    """
    The DataReceived event is fired whenever data is received on a stream from
    the remote peer.
    """

    data: bytes
    "The data which was received."

    stream_id: int
    "The ID of the stream the data was received for."

    stream_ended: bool
    "Whether the STREAM frame had the FIN bit set."


@dataclass
class RequestReceived(HttpEvent):
    """
    The RequestReceived event is fired whenever request headers are received.
    """

    headers: Headers
    "The request headers."

    stream_id: int
    "The ID of the stream the headers were received for."

    stream_ended: bool
    "Whether the STREAM frame had the FIN bit set."


@dataclass
class ResponseReceived(HttpEvent):
    """
    The ResponseReceived event is fired whenever response headers are received.
    """

    headers: Headers
    "The response headers."

    stream_id: int
    "The ID of the stream the headers were received for."

    stream_ended: bool
    "Whether the STREAM frame had the FIN bit set."
