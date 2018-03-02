import asyncio

from . import rtp
from .codecs import get_decoder, get_encoder
from .utils import first_completed, random32


class RTCRtpReceiver:
    async def _run(self, transport, decoder, payload_type):
        while True:
            try:
                data = await transport.recv()
            except ConnectionError:
                break

            # skip RTCP for now
            if rtp.is_rtcp(data):
                continue

            # for now, discard decoded data
            packet = rtp.Packet.parse(data)
            if packet.payload_type == payload_type:
                decoder.decode(packet.payload)


class RTCRtpSender:
    def __init__(self, track=None):
        self._track = track

    @property
    def track(self):
        return self._track

    async def _run(self, transport, encoder, payload_type):
        packet = rtp.Packet(payload_type=payload_type)
        packet.ssrc = random32()
        while True:
            if self._track:
                frame = await self._track.recv()
                packet.payload = encoder.encode(frame)
                packet.marker = 1
                await transport.send(bytes(packet))
                packet.sequence_number += 1
                packet.timestamp += encoder.timestamp_increment
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
        codec = self._codecs[0]
        decoder = get_decoder(codec)
        encoder = get_encoder(codec)

        await first_completed(
            self.receiver._run(transport, decoder=decoder, payload_type=codec.pt),
            self.sender._run(transport, encoder=encoder, payload_type=codec.pt),
            self.__stopped.wait())
