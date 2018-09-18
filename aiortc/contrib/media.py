import asyncio
import math
import threading
import time
import wave

import av
import cv2
import numpy

from ..codecs.h264 import frame_from_avframe
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


def player_worker(loop, container, audio_track, video_track, quit_event):
    audio_fifo = av.audio.fifo.AudioFifo()
    audio_format = av.audio.format.AudioFormat('s16')
    audio_resampler = av.audio.resampler.AudioResampler(
        format=audio_format)

    frame_time = None
    start_time = time.time()

    while not quit_event.is_set():
        try:
            frame = next(container.decode())
        except StopIteration:
            if audio_track:
                audio_track.stop()
            if video_track:
                video_track.stop()
            break

        if frame_time and (time.time() - start_time) < frame_time + 2:
            time.sleep(0.1)

        if isinstance(frame, av.AudioFrame) and audio_track:
            frame_time = frame.time
            if frame.format != audio_format:
                frame = audio_resampler.resample(frame)
            samples_per_frame = int(frame.sample_rate * AUDIO_PTIME)
            audio_fifo.write(frame)
            while True:
                frame = audio_fifo.read(samples_per_frame)
                if frame:
                    frame_time = frame.time
                    frame = AudioFrame(
                        channels=len(frame.layout.channels),
                        data=frame.planes[0].to_bytes(),
                        sample_rate=frame.sample_rate)
                    asyncio.run_coroutine_threadsafe(audio_track._queue.put(
                        (frame, frame_time)), loop)
                else:
                    break
        elif isinstance(frame, av.VideoFrame) and video_track:
            if video_track._queue.qsize() < 30:
                frame_time = frame.time
                frame = frame_from_avframe(frame)
                asyncio.run_coroutine_threadsafe(video_track._queue.put(
                    (frame, frame_time)), loop)


class PlayerAudioTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self._ended = False
        self._queue = asyncio.Queue()
        self._start = None

    async def recv(self):
        frame, frame_time = await self._queue.get()

        # control playback rate
        if self._start is None:
            self._start = time.time() - frame_time
        else:
            wait = self._start + frame_time - time.time()
            await asyncio.sleep(wait)

        return frame

    def stop(self):
        if not self._ended:
            self._ended = True
            self.emit('ended')


class PlayerVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self._ended = False
        self._queue = asyncio.Queue()
        self._start = None

    async def recv(self):
        frame, frame_time = await self._queue.get()

        # control playback rate
        if self._start is None:
            self._start = time.time() - frame_time
        else:
            wait = self._start + frame_time - time.time()
            await asyncio.sleep(wait)

        return frame

    def stop(self):
        if not self._ended:
            self._ended = True
            self.emit('ended')


class MediaPlayer:
    """
    Allows you to read audio and/or video from a file.
    """
    def __init__(self, path):
        self.__container = av.open(file=path, mode='r')
        self.__thread = None
        self.__thread_quit = None

        # examine streams
        self.__audio = None
        self.__video = None
        for stream in self.__container.streams:
            if isinstance(stream, av.audio.stream.AudioStream) and not self.__audio:
                self.__audio = PlayerAudioTrack()
            elif isinstance(stream, av.video.stream.VideoStream) and not self.__video:
                self.__video = PlayerVideoTrack()

    @property
    def audio(self):
        """
        An :class:`AudioStreamTrack` instance if the file contains audio.
        """
        return self.__audio

    @property
    def video(self):
        """
        A :class:`VideoStreamTrack` instance if the file contains video.
        """
        return self.__video

    def play(self):
        """
        Start playback.
        """
        if self.__thread is None:
            self.__thread_quit = threading.Event()
            self.__thread = threading.Thread(
                target=player_worker,
                args=(
                    asyncio.get_event_loop(), self.__container,
                    self.__audio, self.__video,
                    self.__thread_quit))
            self.__thread.start()

    def stop(self):
        """
        Stop playback.
        """
        if self.__thread is not None:
            self.__thread_quit.set()
            self.__thread.join()
            self.__thread = None

        if self.__audio:
            self.__audio.stop()
        if self.__video:
            self.__video.stop()
