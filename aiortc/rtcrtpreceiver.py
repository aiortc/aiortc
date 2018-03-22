import asyncio
import logging

from .codecs import get_decoder
from .exceptions import InvalidStateError
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamTrack
from .rtp import RtcpPacket, RtpPacket, is_rtcp
from .utils import first_completed

logger = logging.getLogger('rtp')


class RemoteStreamTrack(MediaStreamTrack):
    def __init__(self, kind):
        self.kind = kind
        self._queue = asyncio.Queue()

    async def recv(self):
        return await self._queue.get()


class RTCRtpReceiver:
    """
    The :class:`RTCRtpReceiver` interface manages the reception and decoding
    of data for a :class:`MediaStreamTrack`.

    :param: kind: The kind of media (`'audio'` or `'video'`).
    :param: transport: An :class:`RTCDtlsTransport`.
    """
    def __init__(self, kind, transport):
        if transport.state == 'closed':
            raise InvalidStateError

        self._decoders = {}
        self._kind = kind
        self._jitter_buffer = JitterBuffer(capacity=32)
        self._track = None
        self._stopped = asyncio.Event()
        self._transport = transport

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which the media for the receiver's
        track is received.
        """
        return self._transport

    async def receive(self, parameters):
        """
        Attempt to set the parameters controlling the receiving of media.

        :param: parameters: The :class:`RTCRtpParameters` for the receiver.
        """
        for codec in parameters.codecs:
            self._decoders[codec.payloadType] = get_decoder(codec)
        asyncio.ensure_future(self._run())

    def stop(self):
        """
        Irreversibly stop the receiver.
        """
        self._stopped.set()

    async def _handle_rtcp(self, data):
        try:
            packets = RtcpPacket.parse(data)
        except ValueError:
            return
        for packet in packets:
            logger.debug('receiver(%s) < %s' % (self._kind, packet))

    async def _handle_rtp(self, data):
        try:
            packet = RtpPacket.parse(data)
        except ValueError:
            return
        logger.debug('receiver(%s) < %s' % (self._kind, packet))
        if packet.payload_type in self._decoders:
            decoder = self._decoders[packet.payload_type]
            self._jitter_buffer.add(packet.payload, packet.sequence_number, packet.timestamp)

            if self._kind == 'audio':
                # FIXME: audio should use the jitter buffer!
                audio_frame = decoder.decode(packet.payload)
                await self._track._queue.put(audio_frame)
            else:
                # check if we have a complete video frame
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

    async def _run(self):
        while not self._stopped.is_set():
            try:
                data = await first_completed(self.transport.rtp.recv(), self._stopped.wait())
            except ConnectionError:
                self._stopped.set()
                break
            if data is True:
                break

            if is_rtcp(data):
                await self._handle_rtcp(data)
            else:
                await self._handle_rtp(data)

        logger.debug('receiver(%s) - finished' % self._kind)
