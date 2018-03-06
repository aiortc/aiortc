import asyncio
import logging

from .codecs import get_decoder, get_encoder
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamTrack
from .rtp import RtcpPacket, RtpPacket, is_rtcp
from .utils import first_completed, random32

logger = logging.getLogger('rtp')


class RemoteStreamTrack(MediaStreamTrack):
    def __init__(self, kind):
        self.kind = kind
        self._queue = asyncio.Queue()

    async def recv(self):
        return await self._queue.get()


class RTCRtpReceiver:
    def __init__(self, kind):
        self._kind = kind
        self._jitter_buffer = JitterBuffer(capacity=32)
        self._track = None

    async def _run(self, transport, decoder, payload_type):
        while True:
            try:
                data = await transport.recv()
            except ConnectionError:
                logger.debug('receiver(%s) - finished' % self._kind)
                return

            # skip RTCP for now
            if is_rtcp(data):
                for packet in RtcpPacket.parse(data):
                    logger.debug('receiver(%s) < %s' % (self._kind, packet))

            # for now, we discard decoded data
            try:
                packet = RtpPacket.parse(data)
            except ValueError:
                continue
            logger.debug('receiver(%s) < %s' % (self._kind, packet))
            if packet.payload_type == payload_type:
                self._jitter_buffer.add(packet.payload, packet.sequence_number, packet.timestamp)

                if self._kind == 'audio':
                    audio_frame = decoder.decode(packet.payload)
                    await self._track._queue.put(audio_frame)
                else:
                    payloads = []
                    got_frame = False
                    last_timestamp = None
                    for count in range(self._jitter_buffer.capacity):
                        frame = self._jitter_buffer.peek(count)
                        if frame is None:
                            break
                        if last_timestamp is None:
                            last_timestamp = frame.timestamp
                        elif frame.timestamp != last_timestamp:
                            got_frame = True
                            break
                        payloads.append(frame.payload)

                    if got_frame:
                        self._jitter_buffer.remove(count)
                        for video_frame in decoder.decode(*payloads):
                            await self._track._queue.put(video_frame)


class RTCRtpSender:
    def __init__(self, kind):
        self._kind = kind
        self._ssrc = random32()
        self._track = None

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
                        logger.debug('sender(%s) > %s' % (self._kind, packet))
                        await transport.send(bytes(packet))
                    except ConnectionError:
                        logger.debug('sender(%s) - finished' % self._kind)
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
