import asyncio
import math

from pyee import EventEmitter

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCKRATE = 90000
VIDEO_PTIME = 1 / 30  # 30fps


class AudioFrame:
    """
    Audio frame, 16-bit PCM.
    """
    def __init__(self, channels, data, sample_rate, timestamp):
        self.channels = channels
        self.data = data
        self.sample_rate = sample_rate
        self.sample_width = 2
        self.timestamp = timestamp


class VideoFrame:
    """
    Video frame in YUV420 format.
    """
    def __init__(self, width, height, timestamp, data=None):
        self.height = height
        self.width = width
        self.timestamp = timestamp
        if data is None:
            self.data = b'\x00' * math.ceil(width * height * 12 / 8)
        else:
            self.data = data


class MediaStreamTrack(EventEmitter):
    pass


class AudioStreamTrack(MediaStreamTrack):
    """
    The base implementation just reads silence.

    Subclass it to provide a useful implementation.
    """
    kind = 'audio'

    async def recv(self):
        sample_rate = 8000
        samples = int(AUDIO_PTIME * sample_rate)

        timestamp = getattr(self, '_timestamp', 0)
        self._timestamp = timestamp + samples
        await asyncio.sleep(AUDIO_PTIME)

        return AudioFrame(
            channels=1,
            data=b'\x00\x00' * samples,
            sample_rate=sample_rate,
            timestamp=timestamp)


class VideoStreamTrack(MediaStreamTrack):
    """
    The base implementation just reads a 640x480 green frame at 30fps.

    Subclass it to provide a useful implementation.
    """
    kind = 'video'

    async def next_timestamp(self):
        if hasattr(self, '_timestamp'):
            self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCKRATE)
            await asyncio.sleep(VIDEO_PTIME)
        else:
            self._timestamp = 0
        return self._timestamp

    async def recv(self):
        timestamp = await self.next_timestamp()
        return VideoFrame(width=640, height=480, timestamp=timestamp)
