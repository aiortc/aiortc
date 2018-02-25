class RTCRtpSender:
    def __init__(self, track):
        self.__track = track

    @property
    def track(self):
        return self.__track
