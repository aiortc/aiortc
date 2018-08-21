import attr


@attr.s
class RTCStats:
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
    The RTCRemoteOutboundRtpStreamStats dictionary represents the remote
    endpoint's measurement metrics for its outgoing RTP stream.
    """
    remoteTimestamp = attr.ib(default=None)


class RTCStatsReport(dict):
    pass
