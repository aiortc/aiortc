import logging

from aiortc.sdp import DIRECTIONS

logger = logging.getLogger('rtp')


class RTCRtpTransceiver:
    """
    The RTCRtpTransceiver interface describes a permanent pairing of an
    :class:`RTCRtpSender` and an :class:`RTCRtpReceiver`, along with some
    shared state.
    """

    def __init__(self, kind, receiver, sender, direction='sendrecv'):
        self.mid = None
        self.__direction = direction
        self.__kind = kind
        self.__receiver = receiver
        self.__sender = sender
        self.__stopped = False

        self._currentDirection = None
        self._offerDirection = None

    @property
    def currentDirection(self):
        """
        The currently negotiated direction of the transceiver.

        One of `'sendrecv'`, `'sendonly'`, `'recvonly'`, `'inactive'` or `None`.
        """
        return self._currentDirection

    @property
    def direction(self):
        """
        The preferred direction of the transceiver, which will be used in
        :meth:`RTCPeerConnection.createOffer` and :meth:`RTCPeerConnection.createAnswer`.

        One of `'sendrecv'`, `'sendonly'`, `'recvonly'` or `'inactive'`.
        """
        return self.__direction

    @direction.setter
    def direction(self, direction):
        assert direction in DIRECTIONS
        self.__direction = direction

    @property
    def kind(self):
        return self.__kind

    @property
    def receiver(self):
        """
        The :class:`RTCRtpReceiver` that handles receiving and decoding
        incoming media.
        """
        return self.__receiver

    @property
    def sender(self):
        """
        The :class:`RTCRtpSender` responsible for encoding and sending
        data to the remote peer.
        """
        return self.__sender

    @property
    def stopped(self):
        return self.__stopped

    async def stop(self):
        """
        Permanently stops the :class:`RTCRtpTransceiver`.
        """
        await self.__receiver.stop()
        await self.__sender.stop()
        self.__stopped = True
