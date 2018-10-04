import asyncio
import fractions
import uuid

from av import AudioFrame, VideoFrame
from pyee import EventEmitter

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)


def convert_timebase(pts, from_base, to_base):
    if from_base != to_base:
        pts = int(pts * from_base / to_base)
    return pts


class MediaStreamError(Exception):
    pass


class MediaStreamTrack(EventEmitter):
    """
    A single media track within a stream.

    See :class:`AudioStreamTrack` and :class:`VideoStreamTrack`.
    """
    def __init__(self):
        super().__init__()
        self.__ended = False
        self.__id = str(uuid.uuid4())

    @property
    def id(self):
        """
        An automatically generated globally unique ID.
        """
        return self.__id

    @property
    def readyState(self):
        return 'ended' if self.__ended else 'live'

    def stop(self):
        if not self.__ended:
            self.__ended = True
            self.emit('ended')


class AudioStreamTrack(MediaStreamTrack):
    """
    An audio track.
    """
    kind = 'audio'

    async def recv(self):
        """
        Receive the next :class:`~av.audio.frame.AudioFrame`.

        The base implementation just reads silence, subclass
        :class:`AudioStreamTrack` to provide a useful implementation.
        """
        if self.readyState != 'live':
            raise MediaStreamError

        sample_rate = 8000
        samples = int(AUDIO_PTIME * sample_rate)

        timestamp = getattr(self, '_timestamp', 0)
        self._timestamp = timestamp + samples
        await asyncio.sleep(AUDIO_PTIME)

        frame = AudioFrame(format='s16', layout='mono', samples=samples)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = timestamp
        frame.sample_rate = sample_rate
        frame.time_base = fractions.Fraction(1, sample_rate)
        return frame


class VideoStreamTrack(MediaStreamTrack):
    """
    A video stream track.
    """
    kind = 'video'

    async def next_timestamp(self):
        if self.readyState != 'live':
            raise MediaStreamError

        if hasattr(self, '_timestamp'):
            self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
            await asyncio.sleep(VIDEO_PTIME)
        else:
            self._timestamp = 0
        return self._timestamp, VIDEO_TIME_BASE

    async def recv(self):
        """
        Receive the next :class:`~av.video.frame.VideoFrame`.

        The base implementation just reads a 640x480 green frame at 30fps,
        subclass :class:`VideoStreamTrack` to provide a useful implementation.
        """
        pts, time_base = await self.next_timestamp()

        frame = VideoFrame(width=640, height=480)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.time_base = time_base
        return frame
