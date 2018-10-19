import logging

import attr
from pyee import EventEmitter

logger = logging.getLogger('datachannel')


class RTCDataChannel(EventEmitter):
    """
    The :class:`RTCDataChannel` interface represents a network channel which
    can be used for bidirectional peer-to-peer transfers of arbitrary data.

    :param: transport: An :class:`RTCSctpTransport`.
    :param: parameters: An :class:`RTCDataChannelParameters`.
    """

    def __init__(self, transport, parameters, id=None):
        super().__init__()
        self.__bufferedAmount = 0
        self.__bufferedAmountLowThreshold = 0
        self.__id = id
        self.__parameters = parameters
        self.__readyState = 'connecting'
        self.__transport = transport

        if self.__id is None:
            self.__transport._data_channel_open(self)

    @property
    def bufferedAmount(self):
        """
        The number of bytes of data currently queued to be sent over the data channel.
        """
        return self.__bufferedAmount

    @property
    def bufferedAmountLowThreshold(self):
        """
        The number of bytes of buffered outgoing data that is considered "low".
        """
        return self.__bufferedAmountLowThreshold

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
    def ordered(self):
        """
        Indicates whether or not the data channel guarantees in-order delivery of messages.
        """
        return self.__parameters.ordered

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
        """
        return self.__transport

    def close(self):
        """
        Close the data channel.
        """
        self.transport._data_channel_close(self)

    def send(self, data):
        """
        Send `data` across the data channel to the remote peer.
        """
        if not isinstance(data, (str, bytes)):
            raise ValueError('Cannot send unsupported data type: %s' % type(data))

        self.transport._data_channel_send(self, data)

    def _addBufferedAmount(self, amount):
        crosses_threshold = (
            self.__bufferedAmount > self.bufferedAmountLowThreshold and
            self.__bufferedAmount + amount <= self.bufferedAmountLowThreshold
        )
        self.__bufferedAmount += amount
        if crosses_threshold:
            self.emit('bufferedamountlow')

    def _setId(self, id):
        self.__id = id

    def _setReadyState(self, state):
        if state != self.__readyState:
            self.__log_debug('- %s -> %s', self.__readyState, state)
            self.__readyState = state

            if state == 'open':
                self.emit('open')
            elif state == 'closed':
                self.emit('close')

                # no more events will be emitted, so remove all event listeners
                # to facilitate garbage collection.
                self.remove_all_listeners()

    def __log_debug(self, msg, *args):
        logger.debug(str(self.id) + ' ' + msg, *args)


@attr.s
class RTCDataChannelParameters:
    """
    The :class:`RTCDataChannelParameters` dictionary describes the
    configuration of an :class:`RTCDataChannel`.
    """
    label = attr.ib(default='')
    "A name describing the data channel."

    ordered = attr.ib(default=True)
    "Whether the data channel guarantees in-order delivery of messages."

    protocol = attr.ib(default='')
    "The name of the subprotocol in use."
