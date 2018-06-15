import asyncio
import logging

from .codecs import get_decoder
from .exceptions import InvalidStateError
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamTrack

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
        self._started = False
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
        if not self._started:
            for codec in parameters.codecs:
                self._decoders[codec.payloadType] = get_decoder(codec)
            self._transport._register_rtp_receiver(self, parameters)
            self._started = True

    def setTransport(self, transport):
        self._transport = transport

    def stop(self):
        """
        Irreversibly stop the receiver.
        """
        self._stopped.set()

    async def _handle_rtcp_packet(self, packet):
        logger.debug('receiver(%s) < %s' % (self._kind, packet))

    async def _handle_rtp_packet(self, packet):
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
                    video_frames = decoder.decode(payloads)
                    for video_frame in video_frames:
                        await self._track._queue.put(video_frame)
