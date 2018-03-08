import asyncio
import logging

from .codecs import get_encoder
from .exceptions import InvalidStateError
from .rtp import RtpPacket
from .utils import random32

logger = logging.getLogger('rtp')


class RTCRtpSender:
    """
    The RTCRtpSender interface provides the ability to control and obtain
    details about how a particular MediaStreamTrack is encoded and sent to
    a remote peer.
    """
    def __init__(self, trackOrKind):
        if hasattr(trackOrKind, 'kind'):
            self._kind = trackOrKind.kind
            self._track = trackOrKind
        else:
            self._kind = trackOrKind
            self._track = None
        self._ssrc = random32()
        self._transport = None

    @property
    def kind(self):
        return self._kind

    @property
    def track(self):
        """
        The MediaStreamTrack which is being handled by the RTCRtpSender.
        """
        return self._track

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which media data for the track is
        transmitted.
        """
        return self._transport

    def replaceTrack(self, track):
        self._track = track

    def setTransport(self, transport):
        if transport.state == 'closed':
            raise InvalidStateError
        self._transport = transport

    async def _run(self, codec):
        encoder = get_encoder(codec)
        packet = RtpPacket(payload_type=codec.payloadType)
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
                        logger.debug('sender(%s) > %s' % (self._kind, packet))
                        await self.transport.rtp.send(bytes(packet))
                    except ConnectionError:
                        logger.debug('sender(%s) - finished' % self._kind)
                        return
                    packet.sequence_number += 1
                packet.timestamp += encoder.timestamp_increment
            else:
                await asyncio.sleep(0.02)
