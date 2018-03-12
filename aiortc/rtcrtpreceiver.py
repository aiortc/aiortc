import asyncio
import logging

from .codecs import get_decoder
from .exceptions import InvalidStateError
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamTrack
from .rtp import RtcpPacket, RtpPacket, is_rtcp

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
    :param: transport: An :class:`RTCDtlsTransport`
    """
    def __init__(self, kind, transport):
        if transport.state == 'closed':
            raise InvalidStateError

        self._kind = kind
        self._jitter_buffer = JitterBuffer(capacity=32)
        self._track = None
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
        Attempts to set the parameters controlling the receiving of media.
        """
        asyncio.ensure_future(self._run(parameters.codecs[0]))

    def stop(self):
        """
        Irreversibly stop the receiver.
        """
        pass

    async def _run(self, codec):
        decoder = get_decoder(codec)
        while True:
            try:
                data = await self.transport.rtp.recv()
            except ConnectionError:
                logger.debug('receiver(%s) - finished' % self._kind)
                return

            # skip RTCP for now
            if is_rtcp(data):
                for packet in RtcpPacket.parse(data):
                    logger.debug('receiver(%s) < %s' % (self._kind, packet))

            # handle RTP
            try:
                packet = RtpPacket.parse(data)
            except ValueError:
                continue
            logger.debug('receiver(%s) < %s' % (self._kind, packet))
            if packet.payload_type == codec.payloadType:
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
