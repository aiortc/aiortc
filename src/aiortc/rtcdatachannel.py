import logging
from dataclasses import dataclass
from typing import Optional, Union

from pyee.asyncio import AsyncIOEventEmitter

from .exceptions import InvalidStateError

logger = logging.getLogger(__name__)


@dataclass
class RTCDataChannelParameters:
    """
    The :class:`RTCDataChannelParameters` dictionary describes the
    configuration of an :class:`RTCDataChannel`.
    """

    label: str = ""
    "A name describing the data channel."

    maxPacketLifeTime: Optional[int] = None
    "The maximum time in milliseconds during which transmissions are attempted."

    maxRetransmits: Optional[int] = None
    "The maximum number of retransmissions that are attempted."

    ordered: bool = True
    "Whether the data channel guarantees in-order delivery of messages."

    protocol: str = ""
    "The name of the subprotocol in use."

    negotiated: bool = False
    """
    Whether data channel will be negotiated out of-band, where both sides
    create data channel with an agreed-upon ID."""

    id: Optional[int] = None
    """
    An numeric ID for the channel; permitted values are 0-65534.
    If you don't include this option, the user agent will select an ID for you.
    Must be set when negotiating out-of-band.
    """


class RTCDataChannel(AsyncIOEventEmitter):
    """
    The :class:`RTCDataChannel` interface represents a network channel which
    can be used for bidirectional peer-to-peer transfers of arbitrary data.

    :param transport: An :class:`RTCSctpTransport`.
    :param parameters: An :class:`RTCDataChannelParameters`.
    """

    def __init__(
        self, transport, parameters: RTCDataChannelParameters, send_open: bool = True
    ) -> None:
        super().__init__()
        self.__bufferedAmount = 0
        self.__bufferedAmountLowThreshold = 0
        self.__id = parameters.id
        self.__parameters = parameters
        self.__readyState = "connecting"
        self.__transport = transport
        self.__send_open = send_open

        if self.__parameters.negotiated and (
            self.__id is None or self.__id < 0 or self.__id > 65534
        ):
            raise ValueError(
                "ID must be in range 0-65534 "
                "if data channel is negotiated out-of-band"
            )

        if not self.__parameters.negotiated:
            if self.__send_open:
                self.__send_open = False
                self.__transport._data_channel_open(self)
        else:
            self.__transport._data_channel_add_negotiated(self)

    @property
    def bufferedAmount(self) -> int:
        """
        The number of bytes of data currently queued to be sent over the data channel.
        """
        return self.__bufferedAmount

    @property
    def bufferedAmountLowThreshold(self) -> int:
        """
        The number of bytes of buffered outgoing data that is considered "low".
        """
        return self.__bufferedAmountLowThreshold

    @bufferedAmountLowThreshold.setter
    def bufferedAmountLowThreshold(self, value: int) -> None:
        if value < 0 or value > 4294967295:
            raise ValueError(
                "bufferedAmountLowThreshold must be in range 0 - 4294967295"
            )
        self.__bufferedAmountLowThreshold = value

    @property
    def negotiated(self) -> bool:
        """
        Whether data channel was negotiated out-of-band.
        """
        return self.__parameters.negotiated

    @property
    def id(self) -> Optional[int]:
        """
        An ID number which uniquely identifies the data channel.
        """
        return self.__id

    @property
    def label(self) -> str:
        """
        A name describing the data channel.

        These labels are not required to be unique.
        """
        return self.__parameters.label

    @property
    def ordered(self) -> bool:
        """
        Indicates whether or not the data channel guarantees in-order delivery of
        messages.
        """
        return self.__parameters.ordered

    @property
    def maxPacketLifeTime(self) -> Optional[int]:
        """
        The maximum time in milliseconds during which transmissions are attempted.
        """
        return self.__parameters.maxPacketLifeTime

    @property
    def maxRetransmits(self) -> Optional[int]:
        """
        "The maximum number of retransmissions that are attempted.
        """
        return self.__parameters.maxRetransmits

    @property
    def protocol(self) -> str:
        """
        The name of the subprotocol in use.
        """
        return self.__parameters.protocol

    @property
    def readyState(self) -> str:
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

    def close(self) -> None:
        """
        Close the data channel.
        """
        self.transport._data_channel_close(self)

    def send(self, data: Union[bytes, str]) -> None:
        """
        Send `data` across the data channel to the remote peer.
        """
        if self.readyState != "open":
            raise InvalidStateError

        if not isinstance(data, (str, bytes)):
            raise ValueError(f"Cannot send unsupported data type: {type(data)}")

        self.transport._data_channel_send(self, data)

    def _addBufferedAmount(self, amount: int) -> None:
        crosses_threshold = (
            self.__bufferedAmount > self.bufferedAmountLowThreshold
            and self.__bufferedAmount + amount <= self.bufferedAmountLowThreshold
        )
        self.__bufferedAmount += amount
        if crosses_threshold:
            self.emit("bufferedamountlow")

    def _setId(self, id: int) -> None:
        self.__id = id

    def _setReadyState(self, state: str) -> None:
        if state != self.__readyState:
            self.__log_debug("- %s -> %s", self.__readyState, state)
            self.__readyState = state

            if state == "open":
                self.emit("open")
            elif state == "closed":
                self.emit("close")

                # no more events will be emitted, so remove all event listeners
                # to facilitate garbage collection.
                self.remove_all_listeners()

    def __log_debug(self, msg: str, *args) -> None:
        logger.debug(f"RTCDataChannel(%s) {msg}", self.__id, *args)
