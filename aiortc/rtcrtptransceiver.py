import asyncio

from . import rtp
from .utils import first_completed


class RTCRtpReceiver:
    async def _run(self, transport):
        # for now, just drain incoming data
        while True:
            try:
                await transport.recv()
            except ConnectionError:
                break


class RTCRtpSender:
    def __init__(self, track=None):
        self._track = track

    @property
    def track(self):
        return self._track

    async def _run(self, transport):
        packet = rtp.Packet(payload_type=0)
        while True:
            if self._track:
                packet.payload = await self._track.recv()
                await transport.send(bytes(packet))
                packet.sequence_number += 1
                packet.timestamp += len(packet.payload)
            else:
                await asyncio.sleep(0.02)


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
        await first_completed(
            self.receiver._run(transport),
            self.sender._run(transport),
            self.__stopped.wait())
