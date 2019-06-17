from dataclasses import dataclass


class Event:
    pass


@dataclass
class ConnectionIdIssued(Event):
    connection_id: bytes


@dataclass
class ConnectionIdRetired(Event):
    connection_id: bytes


@dataclass
class ConnectionTerminated(Event):
    error_code: int
    frame_type: int
    reason_phrase: str


class HandshakeCompleted(Event):
    pass


@dataclass
class PongReceived(Event):
    uid: int


@dataclass
class StreamDataReceived(Event):
    data: bytes
    end_stream: bool
    stream_id: int


@dataclass
class StreamReset(Event):
    stream_id: int
