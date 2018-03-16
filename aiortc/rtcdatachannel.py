import asyncio

import attr
from pyee import EventEmitter


class RTCDataChannel(EventEmitter):
    """
    The :class:`RTCDataChannel` interface represents a network channel which
    can be used for bidirectional peer-to-peer transfers of arbitrary data.

    :param: transport: An :class:`RTCSctptransport`.
    :param: parameters: An :class:`RTCDataChannelParameters`.
    """

    def __init__(self, transport, parameters, id=None):
        super().__init__()
        self.__id = id
        self.__parameters = parameters
        self.__readyState = 'connecting'
        self.__transport = transport

        if self.__id is None:
            self.__transport._data_channel_open(self)

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
        return self.__parameters.label

    @property
    def protocol(self):
        """
        The name of the subprotocol in use.
        """
        return self.__parameters.protocol

    @property
    def readyState(self):
        """
        A string indicating the current state of the underlying data transport.
        """
        return self.__readyState

    @property
    def transport(self):
        """
        The :class:`RTCSctpTransport` over which data is transmitted.
        transmitted.
        """
        return self.__transport

    def close(self):
        """
        Close the data channel.
        """
        self._setReadyState('closed')

    def send(self, data):
        """
        Send `data` across the data channel to the remote peer.
        """
        if not isinstance(data, (str, bytes)):
            raise ValueError('Cannot send unsupported data type: %s' % type(data))

        asyncio.ensure_future(self.transport._data_channel_send(self, data))

    def _setId(self, id):
        self.__id = id

    def _setReadyState(self, state):
        if state != self.__readyState:
            self.__readyState = state


@attr.s
class RTCDataChannelParameters:
    """
    The :class:`RTCDataChannelParameters` dictionary describes the
    configuration of an :class:`RTCDataChannel`.
    """
    label = attr.ib(default='')
    "A name describing the data channel."

    protocol = attr.ib(default='')
    "The name of the subprotocol in use."
