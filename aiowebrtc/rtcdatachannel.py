import asyncio
from struct import pack

from pyee import EventEmitter


class RTCDataChannel(EventEmitter):
    def __init__(self, id, label, endpoint, loop=None):
        super().__init__(loop=loop)
        self.__id = id
        self.__endpoint = endpoint
        self.__label = label

        # DATA_CHANNEL_OPEN
        data = pack('!BBHLHH', 0x03, 0, 0, 0, len(label), 0) + label.encode('utf8')
        asyncio.ensure_future(self.__endpoint.send(self.id, 50, data))

    def close(self):
        pass

    def send(self, data):
        asyncio.ensure_future(self.__endpoint.send(self.id, 51, data.encode('utf8')))

    @property
    def id(self):
        """
        An ID number which uniquely identifies the data channel.
        """
        return self.__id

    @property
    def label(self):
        """
        A name describing the data channel.

        These labels are not required to be unique.
        """
        return self.__label
