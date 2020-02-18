import datetime

import attr


@attr.s
class RTCStats:
    """
    Base class for statistics.
    """

    timestamp: datetime.datetime = attr.ib()
    "The timestamp associated with this object."
    type: str = attr.ib()
    id: str = attr.ib()


@attr.s
class RTCRtpStreamStats(RTCStats):
    ssrc: int = attr.ib()
    kind: str = attr.ib()
    transportId: str = attr.ib()


@attr.s
class RTCReceivedRtpStreamStats(RTCRtpStreamStats):
    packetsReceived: int = attr.ib()
    packetsLost: int = attr.ib()
    jitter: int = attr.ib()


@attr.s
class RTCSentRtpStreamStats(RTCRtpStreamStats):
    packetsSent: int = attr.ib()
    "Total number of RTP packets sent for this SSRC."
    bytesSent: int = attr.ib()
    "Total number of bytes sent for this SSRC."


@attr.s
class RTCInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    """
    The :class:`RTCInboundRtpStreamStats` dictionary represents the measurement
    metrics for the incoming RTP media stream.
    """

    pass


@attr.s
class RTCRemoteInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    """
    The :class:`RTCRemoteInboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for a particular incoming RTP stream.
    """

    roundTripTime: float = attr.ib()
    fractionLost: float = attr.ib()


@attr.s
class RTCOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCOutboundRtpStreamStats` dictionary represents the measurement
    metrics for the outgoing RTP stream.
    """

    trackId: str = attr.ib()


@attr.s
class RTCRemoteOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCRemoteOutboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for its outgoing RTP stream.
    """

    remoteTimestamp: datetime.datetime = attr.ib(default=None)


@attr.s
class RTCTransportStats(RTCStats):
    packetsSent: int = attr.ib()
    "Total number of packets sent over this transport."
    packetsReceived: int = attr.ib()
    "Total number of packets received over this transport."
    bytesSent: int = attr.ib()
    "Total number of bytes sent over this transport."
    bytesReceived: int = attr.ib()
    "Total number of bytes received over this transport."
    iceRole: str = attr.ib()
    "The current value of :attr:`RTCIceTransport.role`."
    dtlsState: str = attr.ib()
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
