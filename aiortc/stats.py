import attr


@attr.s
class RTCStats:
    """
    Base class for statistics.
    """
    timestamp = attr.ib()
    "The timestamp associated with this object."
    type = attr.ib()
    id = attr.ib()


@attr.s
class RTCRtpStreamStats(RTCStats):
    ssrc = attr.ib()
    kind = attr.ib()
    transportId = attr.ib()


@attr.s
class RTCReceivedRtpStreamStats(RTCRtpStreamStats):
    packetsReceived = attr.ib()
    packetsLost = attr.ib()
    jitter = attr.ib()


@attr.s
class RTCSentRtpStreamStats(RTCRtpStreamStats):
    packetsSent = attr.ib()
    "Total number of RTP packets sent for this SSRC."
    bytesSent = attr.ib()
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
    roundTripTime = attr.ib()
    fractionLost = attr.ib()


@attr.s
class RTCOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCOutboundRtpStreamStats` dictionary represents the measurement
    metrics for the outgoing RTP stream.
    """
    trackId = attr.ib(type=str)


@attr.s
class RTCRemoteOutboundRtpStreamStats(RTCSentRtpStreamStats):
    """
    The :class:`RTCRemoteOutboundRtpStreamStats` dictionary represents the remote
    endpoint's measurement metrics for its outgoing RTP stream.
    """
    remoteTimestamp = attr.ib(default=None)


@attr.s
class RTCTransportStats(RTCStats):
    packetsSent = attr.ib()  # type: int
    "Total number of packets sent over this transport."
    packetsReceived = attr.ib()  # type: int
    "Total number of packets received over this transport."
    bytesSent = attr.ib()  # type: int
    "Total number of bytes sent over this transport."
    bytesReceived = attr.ib()  # type: int
    "Total number of bytes received over this transport."
    iceRole = attr.ib()  # type: str
    "The current value of :attr:`RTCIceTransport.role`."
    dtlsState = attr.ib()  # type: str
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
    def add(self, stats):
        self[stats.id] = stats
