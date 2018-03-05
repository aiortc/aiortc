import asyncio
import math


class AudioFrame:
    """
    Audio frame, 16-bit PCM.
    """
    def __init__(self, channels, data, sample_rate):
        self.channels = channels
        self.data = data
        self.sample_rate = sample_rate
        self.sample_width = 2


class VideoFrame:
    """
    Video frame in YUV420 format.
    """
    def __init__(self, width, height, data=None):
        self.height = height
        self.width = width
        if data is None:
            self.data = b'\x00' * math.ceil(width * height * 12 / 8)
        else:
            self.data = data


class MediaStreamTrack:
    pass


class AudioStreamTrack(MediaStreamTrack):
    """
    The base implementation just reads silence.

    Subclass it to provide a useful implementation.
    """
    kind = 'audio'

    async def recv(self):
        await asyncio.sleep(0.02)
        return AudioFrame(channels=1, data=b'\x00' * 160, sample_rate=8000)


class VideoStreamTrack(MediaStreamTrack):
    """
    The base implementation just reads a green frame.

    Subclass it to provide a useful implementation.
    """
    kind = 'video'

    async def recv(self):
        await asyncio.sleep(0.02)
        return VideoFrame(width=320, height=240, data=b'\x00' * 115200)
