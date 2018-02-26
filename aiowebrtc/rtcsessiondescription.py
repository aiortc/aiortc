class RTCSessionDescription:
    def __init__(self, sdp, type):
        if type not in ['offer', 'pranswer', 'answer', 'rollback']:
            raise ValueError('Unexpected SDP type "%s"' % type)
        self.__sdp = sdp
        self.__type = type

    @property
    def sdp(self):
        return self.__sdp

    @property
    def type(self):
        return self.__type
