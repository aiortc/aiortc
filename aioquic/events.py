from dataclasses import dataclass
from typing import Optional


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
    """
    This event occurs when the QUIC connection is terminated.
    """

    error_code: int
    frame_type: int
    reason_phrase: str


@dataclass
class HandshakeCompleted(Event):
    """
    This event occurs when the TLS handshake completes.
    """

    alpn_protocol: Optional[str]


@dataclass
class PongReceived(Event):
    """
    This event occurs when the response to a PING frame is received.
    """

    uid: int


@dataclass
class StreamDataReceived(Event):
    """
    This event ocurs when data is received on a stream.
    """

    data: bytes
    end_stream: bool
    stream_id: int


@dataclass
class StreamReset(Event):
    """
    This event occurs when a stream is reset.
    """

    stream_id: int
