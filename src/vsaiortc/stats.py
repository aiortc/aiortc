import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass
class RTCStats:
    """
    Base class for statistics.
    """

    timestamp: datetime.datetime
    "The timestamp associated with this object."
    type: str
    id: str


@dataclass
class RTCRtpStreamStats(RTCStats):
    ssrc: int
    kind: str
    transportId: str


@dataclass
class RTCReceivedRtpStreamStats(RTCRtpStreamStats):
    packetsReceived: int
    packetsLost: int
    jitter: int


@dataclass
class RTCSentRtpStreamStats(RTCRtpStreamStats):
    packetsSent: int
    "Total number of RTP packets sent for this SSRC."
    bytesSent: int
    "Total number of bytes sent for this SSRC."


@dataclass
class RTCInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    """
    The :class:`RTCInboundRtpStreamStats` dictionary represents the measurement
    metrics for the incoming RTP media stream.
    """

    pass


@dataclass
class RTCRemoteInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    """
    The :class:`RTCRemoteInboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for a particular incoming RTP stream.
    """

    roundTripTime: float
    fractionLost: float


@dataclass
class RTCOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCOutboundRtpStreamStats` dictionary represents the measurement
    metrics for the outgoing RTP stream.
    """

    trackId: str


@dataclass
class RTCRemoteOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCRemoteOutboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for its outgoing RTP stream.
    """

    remoteTimestamp: Optional[datetime.datetime] = None


@dataclass
class RTCTransportStats(RTCStats):
    packetsSent: int
    "Total number of packets sent over this transport."
    packetsReceived: int
    "Total number of packets received over this transport."
    bytesSent: int
    "Total number of bytes sent over this transport."
    bytesReceived: int
    "Total number of bytes received over this transport."
    iceRole: str
    "The current value of :attr:`RTCIceTransport.role`."
    dtlsState: str
    "The current value of :attr:`RTCDtlsTransport.state`."


class RTCStatsReport(dict):
    """
    Provides statistics data about WebRTC connections as returned by the
    :meth:`RTCPeerConnection.getStats()`, :meth:`RTCRtpReceiver.getStats()`
    and :meth:`RTCRtpSender.getStats()` coroutines.

    This object consists of a mapping of string identifiers to objects which
    are instances of:

    - :class:`RTCInboundRtpStreamStats`
    - :class:`RTCOutboundRtpStreamStats`
    - :class:`RTCRemoteInboundRtpStreamStats`
    - :class:`RTCRemoteOutboundRtpStreamStats`
    - :class:`RTCTransportStats`
    """

    def add(self, stats: RTCStats) -> None:
        self[stats.id] = stats
