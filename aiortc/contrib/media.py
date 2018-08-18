import asyncio
import math
import time
import wave

import cv2
import numpy

from ..mediastreams import (AudioFrame, AudioStreamTrack, VideoFrame,
                            VideoStreamTrack)

AUDIO_PTIME = 0.020  # 20ms audio packetization


def frame_from_bgr(data_bgr):
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    return VideoFrame(width=data_bgr.shape[1], height=data_bgr.shape[0], data=data_yuv.tobytes())


def frame_from_gray(data_gray):
    data_bgr = cv2.cvtColor(data_gray, cv2.COLOR_GRAY2BGR)
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    return VideoFrame(width=data_bgr.shape[1], height=data_bgr.shape[0], data=data_yuv.tobytes())


def frame_to_bgr(frame):
    data_flat = numpy.frombuffer(frame.data, numpy.uint8)
    data_yuv = data_flat.reshape((math.ceil(frame.height * 12 / 8), frame.width))
    return cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_I420)


class AudioFileTrack(AudioStreamTrack):
    """
    An AudioStreamTrack subclass for reading audio from a WAV file.
    """
    def __init__(self, path):
        self.last = None
        self.reader = wave.open(path, 'rb')
        assert self.reader.getsampwidth() == 2, 'Only 16-bit samples are supported'
        self.frames_per_packet = int(self.reader.getframerate() * AUDIO_PTIME)

    async def recv(self):
        # as we are reading audio from a file and not using a "live" source,
        # we need to control the rate at which audio is sent
        if self.last:
            now = time.time()
            await asyncio.sleep(self.last + AUDIO_PTIME - now)
        self.last = time.time()

        data = self.reader.readframes(self.frames_per_packet)
        frames = len(data) // (self.reader.getnchannels() * self.reader.getsampwidth())
        missing = self.frames_per_packet - frames
        if missing:
            self.reader.rewind()
            data += self.reader.readframes(missing)

        return AudioFrame(
            channels=self.reader.getnchannels(),
            data=data,
            sample_rate=self.reader.getframerate())


class VideoFileTrack(VideoStreamTrack):
    """
    A VideoStreamTrack subclass for reading video from a file.
    """
    def __init__(self, path):
        self.cap = cv2.VideoCapture(path)
        self.last = None
        self.ptime = 1 / self.cap.get(cv2.CAP_PROP_FPS)

    async def recv(self):
        # as we are reading audio from a file and not using a "live" source,
        # we need to control the rate at which audio is sent
        if self.last:
            now = time.time()
            await asyncio.sleep(self.last + self.ptime - now)
        self.last = time.time()

        ret, frame = self.cap.read()
        if not ret:
            # loop
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self.cap.read()

        return frame_from_bgr(frame)
