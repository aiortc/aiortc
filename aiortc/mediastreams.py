import asyncio
import cv2
import math
import numpy


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

    @classmethod
    def from_yuv(cls, height, width, data_yuv):
        data = data_yuv.tobytes()
        return cls(width, height, data)

    @classmethod
    def from_bgr(cls, height, width, data_bgr):
        data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_YV12)
        return cls.from_yuv(width, height, data_yuv)

    def to_yuv(self):
        # truncating the data as a workaround for #10
        data_len = math.ceil(self.height * 12 / 8 * self.width)
        data = self.data[0:data_len]
        data_flat = numpy.frombuffer(data, numpy.uint8)
        data_yuv = data_flat.reshape((math.ceil(self.height * 12 / 8), self.width))
        return data_yuv

    def to_bgr(self):
        data_yuv = self.to_yuv()
        data_bgr = cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_YV12)
        return data_bgr


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
