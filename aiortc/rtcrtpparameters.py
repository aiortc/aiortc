import attr


@attr.s
class RTCRtpCodecParameters:
    name = attr.ib(type=str)
    clockRate = attr.ib(type=int)
    channels = attr.ib(default=None)
    payloadType = attr.ib(default=None)

    def clone(self, payloadType):
        return RTCRtpCodecParameters(
            name=self.name, clockRate=self.clockRate,
            channels=self.channels, payloadType=payloadType)

    def __str__(self):
        s = '%s/%d' % (self.name, self.clockRate)
        if self.channels == 2:
            s += '/2'
        return s


@attr.s
class RTCRtpCapabilities:
    codecs = attr.ib(default=attr.Factory(list))


@attr.s
class RTCRtpParameters:
    codecs = attr.ib(default=attr.Factory(list))
