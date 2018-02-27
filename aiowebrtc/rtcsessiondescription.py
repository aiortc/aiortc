class RTCSessionDescription:
    """
    The RTCSessionDescription interface describes one end of a connection
    and how it's configured.
    """

    def __init__(self, sdp, type):
        if type not in ['offer', 'pranswer', 'answer', 'rollback']:
            raise ValueError('Unexpected SDP type "%s"' % type)
        self.__sdp = sdp
        self.__type = type

    @property
    def sdp(self):
        """
        A string containing the session description's SDP.
        """
        return self.__sdp

    @property
    def type(self):
        """
        A string describing the session description's type.
        """
        return self.__type
