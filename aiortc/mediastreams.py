import asyncio


class AudioFrame:
    """
    Audio frame, 16-bit PCM at 8 kHz.
    """
    def __init__(self, data):
        self.data = data


class VideoFrame:
    """
    Video frame in YUV420 format.
    """
    def __init__(self, height, width, data):
        self.height = height
        self.width = width
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
        return AudioFrame(data=b'\x00' * 160)


class VideoStreamTrack(MediaStreamTrack):
    kind = 'video'

    async def recv(self):
        await asyncio.sleep(0.02)
        raise VideoFrame(width=320, height=240, data=b'\x00' * 115200)
