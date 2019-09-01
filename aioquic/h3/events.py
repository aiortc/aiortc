from dataclasses import dataclass
from typing import List, Optional, Tuple

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

    push_id: Optional[int] = None
    "The Push ID or `None` if this is not a push."


@dataclass
class HeadersReceived(HttpEvent):
    """
    The HeadersReceived event is fired whenever headers are received.
    """

    headers: Headers
    "The headers."

    stream_id: int
    "The ID of the stream the headers were received for."

    stream_ended: bool
    "Whether the STREAM frame had the FIN bit set."

    push_id: Optional[int] = None
    "The Push ID or `None` if this is not a push."


@dataclass
class PushPromiseReceived(HttpEvent):
    """
    The PushedStreamReceived event is fired whenever a pushed stream has been
    received from the remote peer.
    """

    headers: Headers
    "The request headers."

    push_id: int
    "The Push ID of the push promise."

    stream_id: int
    "The Stream ID of the stream that the push is related to."


# backwards compatibility

RequestReceived = ResponseReceived = HeadersReceived
