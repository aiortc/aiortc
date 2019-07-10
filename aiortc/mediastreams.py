import asyncio
import fractions
import time
import uuid

from av import AudioFrame, VideoFrame
from pyee import EventEmitter

AUDIO_PTIME = 0.020  # 20ms audio packetization
AUDIO_CLOCK_RATE = 8000
AUDIO_TIME_BASE = fractions.Fraction(1, AUDIO_CLOCK_RATE)
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
        return "ended" if self.__ended else "live"

    def stop(self):
        if not self.__ended:
            self.__ended = True
            self.emit("ended")

            # no more events will be emitted, so remove all event listeners
            # to facilitate garbage collection.
            self.remove_all_listeners()


class TimedMediaStreamTrack(MediaStreamTrack):
    time_base = None
    sample_rate = None
    ptime = None

    def __init__(self):
        super().__init__()
        self.__timestamp = None
        self.__start = None

    async def next_timestamp(self):
        if self.readyState != "live":
            raise MediaStreamError

        if self.__timestamp is None:
            self.__timestamp = 0
            self.__start = time.time()
        else:
            self.__timestamp += int(self.ptime * self.sample_rate)
            wait = self.__start + (self.__timestamp / self.sample_rate) - time.time()
            await asyncio.sleep(wait)

        return self.__timestamp, self.time_base


class AudioStreamTrack(TimedMediaStreamTrack):
    """
    An audio track.
    """

    kind = "audio"

    sample_rate = AUDIO_CLOCK_RATE
    time_base = AUDIO_TIME_BASE
    ptime = AUDIO_PTIME

    async def recv(self):
        """
        Receive the next :class:`~av.audio.frame.AudioFrame`.

        The base implementation just reads silence, subclass
        :class:`AudioStreamTrack` to provide a useful implementation.
        """
        pts, time_base = await self.next_timestamp()

        frame = AudioFrame(
            format="s16", layout="mono", samples=int(self.ptime * self.sample_rate)
        )
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.sample_rate = self.sample_rate
        frame.time_base = time_base
        return frame


class VideoStreamTrack(TimedMediaStreamTrack):
    """
    A video stream track.
    """

    kind = "video"

    sample_rate = VIDEO_CLOCK_RATE
    time_base = VIDEO_TIME_BASE
    ptime = VIDEO_PTIME

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
