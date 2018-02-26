class RTCRtpReceiver:
    pass


class RTCRtpSender:
    def __init__(self, track):
        self.__track = track

    @property
    def track(self):
        return self.__track


class RTCRtpTransceiver:
    def __init__(self, receiver, sender):
        self.__receiver = receiver
        self.__sender = sender

    @property
    def receiver(self):
        return self.__receiver

    @property
    def sender(self):
        return self.__sender
