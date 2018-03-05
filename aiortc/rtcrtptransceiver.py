import asyncio
import logging

from .codecs import get_decoder, get_encoder
from .rtp import RtcpPacket, RtpPacket, is_rtcp
from .utils import first_completed, random32

logger = logging.getLogger('rtp')


class RTCRtpReceiver:
    async def _run(self, transport, decoder, payload_type):
        while True:
            try:
                data = await transport.recv()
            except ConnectionError:
                break

            # skip RTCP for now
            if is_rtcp(data):
                for packet in RtcpPacket.parse(data):
                    logger.debug('receiver < %s' % packet)

            # for now, discard decoded data
            packet = RtpPacket.parse(data)
            if packet.payload_type == payload_type:
                decoder.decode(packet.payload)

        logger.debug('receiver - finished')


class RTCRtpSender:
    def __init__(self, track=None):
        self._ssrc = random32()
        self._track = track

    @property
    def track(self):
        return self._track

    async def _run(self, transport, encoder, payload_type):
        packet = RtpPacket(payload_type=payload_type)
        while True:
            if self._track:
                frame = await self._track.recv()
                packet.ssrc = self._ssrc
                payloads = encoder.encode(frame)
                if not isinstance(payloads, list):
                    payloads = [payloads]
                for i, payload in enumerate(payloads):
                    packet.payload = payload
                    packet.marker = (i == len(payloads) - 1) and 1 or 0
                    try:
                        await transport.send(bytes(packet))
                    except ConnectionError:
                        return
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
