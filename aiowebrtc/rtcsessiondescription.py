class RTCSessionDescription:
    def __init__(self, sdp, type):
        self.__sdp = sdp
        self.__type = type

    @property
    def sdp(self):
        return self.__sdp

    @property
    def type(self):
        return self.__type
