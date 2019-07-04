from dataclasses import dataclass
from typing import List, Tuple

Headers = List[Tuple[bytes, bytes]]


class Event:
    pass


@dataclass
class DataReceived(Event):
    data: bytes
    stream_id: int
    stream_ended: bool


@dataclass
class RequestReceived(Event):
    headers: Headers
    stream_id: int
    stream_ended: bool


@dataclass
class ResponseReceived(Event):
    headers: Headers
    stream_id: int
    stream_ended: bool
