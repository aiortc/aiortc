class RTCRtpCodecParameters:
    def __init__(self, name, clockRate, channels=None, payloadType=None):
        self.name = name
        self.clockRate = clockRate
        self.channels = channels
        self.payloadType = payloadType

    def clone(self, payloadType):
        return RTCRtpCodecParameters(
            name=self.name, clockRate=self.clockRate,
            channels=self.channels, payloadType=payloadType)

    def __str__(self):
        s = '%s/%d' % (self.name, self.clockRate)
        if self.channels == 2:
            s += '/2'
        return s


class RTCRtpParameters:
    def __init__(self, codecs=[]):
        self.codecs = codecs
