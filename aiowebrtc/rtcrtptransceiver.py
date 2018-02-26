class RTCRtpReceiver:
    pass


class RTCRtpSender:
    def __init__(self, track=None):
        self._track = track

    @property
    def track(self):
        return self._track


class RTCRtpTransceiver:
    def __init__(self, receiver, sender):
        self.__receiver = receiver
        self.__sender = sender

    @property
    def direction(self):
        if self.sender.track:
            return 'sendrecv'
        else:
            return 'recvonly'

    @property
    def receiver(self):
        return self.__receiver

    @property
    def sender(self):
        return self.__sender
