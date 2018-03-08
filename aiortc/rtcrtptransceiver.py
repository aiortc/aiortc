import asyncio
import logging

from .codecs import get_decoder, get_encoder
from .utils import first_completed

logger = logging.getLogger('rtp')


class RTCRtpTransceiver:
    """
    The RTCRtpTransceiver interface describes a permanent pairing of an
    :class:`RTCRtpSender` and an :class:`RTCRtpReceiver`, along with some
    shared state.
    """

    def __init__(self, receiver, sender):
        self.__receiver = receiver
        self.__sender = sender
        self.__stopped = asyncio.Event()

    @property
    def direction(self):
        if self.sender.track:
            return 'sendrecv'
        else:
            return 'recvonly'

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

    async def stop(self):
        """
        Permanently stops the :class:`RTCRtpTransceiver`.
        """
        self.__stopped.set()

    async def _run(self, transport):
        codec = self._codecs[0]
        decoder = get_decoder(codec)
        encoder = get_encoder(codec)

        self.receiver.setTransport(transport)
        self.sender.setTransport(transport)
        await first_completed(
            self.receiver._run(decoder=decoder, payload_type=codec.pt),
            self.sender._run(encoder=encoder, payload_type=codec.pt),
            self.__stopped.wait())
