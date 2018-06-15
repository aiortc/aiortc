import attr


@attr.s
class RTCStats:
    timestamp = attr.ib()
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
    bytesSent = attr.ib()


@attr.s
class RTCRemoteInboundRtpStreamStats(RTCReceivedRtpStreamStats):
    localId = attr.ib()
    roundTripTime = attr.ib()
    fractionLost = attr.ib()


@attr.s
class RTCRemoteOutboundRtpStreamStats(RTCSentRtpStreamStats):
    localId = attr.ib(type=str)
    remoteTimestamp = attr.ib(default=None)
