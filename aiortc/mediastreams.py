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
    Video frame in YUV420 bytes format.
    """
    def __init__(self, height, width, data=None):
        self.height = height
        self.width = width
        if data is None:
            self.data = b'\x00' * math.ceil(height * 12 / 8 * width)
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
        return VideoFrame(height=240, width=320, data=b'\x00' * 115200)
