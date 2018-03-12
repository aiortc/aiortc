import asyncio
from struct import pack, unpack

from pyee import EventEmitter

# message types
DATA_CHANNEL_ACK = 2
DATA_CHANNEL_OPEN = 3

# channel types
DATA_CHANNEL_RELIABLE = 0

WEBRTC_DCEP = 50
WEBRTC_STRING = 51
WEBRTC_BINARY = 53
WEBRTC_STRING_EMPTY = 56
WEBRTC_BINARY_EMPTY = 57


class DataChannelManager:
    def __init__(self, pc, endpoint):
        self.channels = {}
        self.endpoint = endpoint
        self.pc = pc
        if endpoint.is_server:
            self.stream_id = 0
        else:
            self.stream_id = 1

    def create_channel(self, label, protocol):
        # register channel
        channel = RTCDataChannel(id=self.stream_id, label=label, protocol=protocol,
                                 manager=self, readyState='connecting')
        self.channels[channel.id] = channel
        self.stream_id += 2

        # open channel
        data = pack('!BBHLHH', DATA_CHANNEL_OPEN, DATA_CHANNEL_RELIABLE,
                    0, 0, len(label), len(protocol))
        data += label.encode('utf8')
        data += protocol.encode('utf8')
        asyncio.ensure_future(self.endpoint.send(channel.id, WEBRTC_DCEP, data))

        return channel

    def send(self, channel, data):
        if data == '':
            asyncio.ensure_future(self.endpoint.send(channel.id, WEBRTC_STRING_EMPTY, b'\x00'))
        elif isinstance(data, str):
            asyncio.ensure_future(self.endpoint.send(channel.id, WEBRTC_STRING,
                                                     data.encode('utf8')))
        elif data == b'':
            asyncio.ensure_future(self.endpoint.send(channel.id, WEBRTC_BINARY_EMPTY, b'\x00'))
        elif isinstance(data, bytes):
            asyncio.ensure_future(self.endpoint.send(channel.id, WEBRTC_BINARY, data))
        else:
            raise ValueError('Cannot send unsupported data type: %s' % type(data))

    async def run(self, endpoint):
        self.endpoint = endpoint
        while True:
            try:
                stream_id, pp_id, data = await self.endpoint.recv()
            except ConnectionError:
                return
            if pp_id == WEBRTC_DCEP and len(data):
                msg_type = unpack('!B', data[0:1])[0]
                if msg_type == DATA_CHANNEL_OPEN and len(data) >= 12:
                    # FIXME : one side should be using even IDs, the other odd IDs
                    # assert (stream_id % 2) != (self.stream_id % 2)
                    assert stream_id not in self.channels

                    (msg_type, channel_type, priority, reliability,
                     label_length, protocol_length) = unpack('!BBHLHH', data[0:12])
                    pos = 12
                    label = data[pos:pos + label_length].decode('utf8')
                    pos += label_length
                    protocol = data[pos:pos + protocol_length].decode('utf8')

                    # register channel
                    channel = RTCDataChannel(id=stream_id, label=label, protocol=protocol,
                                             manager=self, readyState='open')
                    self.channels[stream_id] = channel

                    # send ack
                    await self.endpoint.send(channel.id, WEBRTC_DCEP, pack('!B', DATA_CHANNEL_ACK))

                    # emit channel
                    self.pc.emit('datachannel', channel)
                elif msg_type == DATA_CHANNEL_ACK:
                    assert stream_id in self.channels
                    channel = self.channels[stream_id]
                    channel._setReadyState('open')
            elif pp_id == WEBRTC_STRING and stream_id in self.channels:
                # emit message
                self.channels[stream_id].emit('message', data.decode('utf8'))
            elif pp_id == WEBRTC_STRING_EMPTY and stream_id in self.channels:
                # emit message
                self.channels[stream_id].emit('message', '')
            elif pp_id == WEBRTC_BINARY and stream_id in self.channels:
                # emit message
                self.channels[stream_id].emit('message', data)
            elif pp_id == WEBRTC_BINARY_EMPTY and stream_id in self.channels:
                # emit message
                self.channels[stream_id].emit('message', b'')


class RTCDataChannel(EventEmitter):
    """
    The :class:`RTCDataChannel` interface represents a network channel which
    can be used for bidirectional peer-to-peer transfers of arbitrary data.
    """

    def __init__(self, id, label, protocol, manager, readyState):
        super().__init__()
        self.__id = id
        self.__label = label
        self.__manager = manager
        self.__protocol = protocol
        self.__readyState = readyState

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

    @property
    def protocol(self):
        """
        The name of the subprotocol in use.
        """
        return self.__protocol

    @property
    def readyState(self):
        """
        A string indicating the current state of the underlying data transport.
        """
        return self.__readyState

    def close(self):
        """
        Close the data channel.
        """
        self._setReadyState('closed')

    def send(self, data):
        """
        Send `data` across the data channel to the remote peer.
        """
        self.__manager.send(self, data)

    def _setReadyState(self, state):
        if state != self.__readyState:
            self.__readyState = state
