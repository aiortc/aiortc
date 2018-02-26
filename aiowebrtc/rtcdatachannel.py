from pyee import EventEmitter


class RTCDataChannel(EventEmitter):
    def __init__(self, label, loop=None):
        super().__init__(loop=loop)
        self.__label = label

    def close(self):
        pass

    @property
    def label(self):
        """
        A name describing the data channel.

        These labels are not required to be unique.
        """
        return self.__label
