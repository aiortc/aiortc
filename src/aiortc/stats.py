import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass(kw_only=True)
class RTCStats:
    """
    Base class for statistics.
    """

    timestamp: datetime.datetime
    "The timestamp associated with this object."
    type: str
    "The type of this object, such as `inbound-rtp`."
    id: str
    "The unique identifier of this object, which stays the same across reports."


@dataclass(kw_only=True)
class RTCRtpStreamStats(RTCStats):
    ssrc: int
    "The SSRC of the RTP stream described by this object."
    kind: str
    "Either `audio` or `video`."
    transportId: str
    "The ID of the statistics of the transport carrying this stream."


@dataclass(kw_only=True)
class RTCReceivedRtpStreamStats(RTCRtpStreamStats):
    packetsReceived: int
    "Total number of RTP packets received for this SSRC, including retransmissions."
    packetsLost: int
    "Total number of RTP packets lost for this SSRC, which can be negative."
    jitter: float
    "Packet jitter in seconds."


@dataclass(kw_only=True)
class RTCSentRtpStreamStats(RTCRtpStreamStats):
    packetsSent: int
    "Total number of RTP packets sent for this SSRC."
    bytesSent: int
    "Total number of bytes sent for this SSRC."


@dataclass(kw_only=True)
class RTCInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    """
    The :class:`RTCInboundRtpStreamStats` dictionary represents the measurement
    metrics for the incoming RTP media stream.
    """

    trackIdentifier: str
    "The identifier of the track which receives this SSRC."
    remoteId: Optional[str] = None
    "The ID of the remote outbound statistics, once an RTCP SR was received."
    bytesReceived: int
    "Total number of payload bytes received for this SSRC, including retransmissions."
    nackCount: int
    "Total number of NACK packets sent for this SSRC."


@dataclass(kw_only=True)
class RTCRemoteInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    """
    The :class:`RTCRemoteInboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for a particular incoming RTP stream.
    """

    localId: Optional[str] = None
    "The ID of the local outbound statistics for this SSRC."
    roundTripTime: Optional[float] = None
    "Estimated round trip time in seconds, once an RTCP RR echoed a sender report."
    fractionLost: float
    "Fraction of packets lost, between 0 and 1, from the last RTCP receiver report."


@dataclass(kw_only=True)
class RTCOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCOutboundRtpStreamStats` dictionary represents the measurement
    metrics for the outgoing RTP stream.
    """

    # This is not a webrtc-stats member and needs to be replaced by mediaSourceId.
    trackId: str
    remoteId: Optional[str] = None
    "The ID of the remote inbound statistics, once an RTCP RR was received."
    framesSent: Optional[int] = None
    "Total number of frames sent for this SSRC, for video only."
    framesEncoded: Optional[int] = None
    "Total number of frames encoded for this SSRC, for video only."
    nackCount: int
    "Total number of NACK packets received for this SSRC."


@dataclass(kw_only=True)
class RTCRemoteOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCRemoteOutboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for its outgoing RTP stream.
    """

    localId: Optional[str] = None
    "The ID of the local inbound statistics for this SSRC."
    remoteTimestamp: Optional[datetime.datetime] = None
    "The time at which the remote endpoint sent the RTCP sender report."


@dataclass(kw_only=True)
class RTCTransportStats(RTCStats):
    """
    The :class:`RTCTransportStats` dictionary represents the measurement metrics
    for a transport.
    """

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
