import logging

from aiortc.codecs import get_capabilities
from aiortc.sdp import DIRECTIONS

logger = logging.getLogger('rtp')


class RTCRtpTransceiver:
    """
    The RTCRtpTransceiver interface describes a permanent pairing of an
    :class:`RTCRtpSender` and an :class:`RTCRtpReceiver`, along with some
    shared state.
    """

    def __init__(self, kind, receiver, sender, direction='sendrecv'):
        self.__direction = direction
        self.__kind = kind
        self.__mid = None
        self.__mline_index = None
        self.__receiver = receiver
        self.__sender = sender
        self.__stopped = False

        self._currentDirection = None
        self._offerDirection = None
        self._preferred_codecs = []

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
    def mid(self):
        return self.__mid

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

    def setCodecPreferences(self, codecs):
        """
        Override the default codec preferences.

        See :meth:`RTCRtpSender.getCapabilities` and :meth:`RTCRtpReceiver.getCapabilities`
        for the supported codecs.

        :param: codecs: A list of :class:`RTCRtpCodecCapability`, in decreasing order
                        of preference. If empty, restores the default preferences.
        """
        if not codecs:
            self._preferred_codecs = []

        capabilities = get_capabilities(self.kind).codecs
        unique = []
        for codec in reversed(codecs):
            if codec not in capabilities:
                raise ValueError('Codec is not in capabilities')
            if codec not in unique:
                unique.insert(0, codec)
        self._preferred_codecs = unique

    async def stop(self):
        """
        Permanently stops the :class:`RTCRtpTransceiver`.
        """
        await self.__receiver.stop()
        await self.__sender.stop()
        self.__stopped = True

    def _set_mid(self, mid):
        self.__mid = mid

    def _get_mline_index(self):
        return self.__mline_index

    def _set_mline_index(self, idx):
        self.__mline_index = idx
