import asyncio
import fractions
import math
import threading
import time

import av
import cv2
import numpy

from ..codecs.h264 import video_frame_from_avframe, video_frame_to_avframe
from ..mediastreams import (AUDIO_PTIME, AudioFrame, AudioStreamTrack,
                            VideoFrame, VideoStreamTrack)


def audio_frame_from_avframe(av_frame):
    """
    Convert an av.AudioFrame to aiortc.AudioFrame.
    """
    return AudioFrame(
        channels=len(av_frame.layout.channels),
        data=av_frame.planes[0].to_bytes(),
        sample_rate=av_frame.sample_rate,
        timestamp=av_frame.pts)


def audio_frame_to_avframe(frame):
    """
    Convert an aiortc.AudioFrame to av.AudioFrame.
    """
    assert frame.channels in [1, 2]
    assert frame.sample_width in [1, 2, 4]

    samples = len(frame.data) // (frame.channels * frame.sample_width)
    av_frame = av.AudioFrame(
        format='s%d' % (8 * frame.sample_width),
        layout='stereo' if frame.channels == 2 else 'mono',
        samples=samples)
    av_frame.planes[0].update(frame.data)
    av_frame.sample_rate = frame.sample_rate
    av_frame.time_base = fractions.Fraction(1, frame.sample_rate)
    return av_frame


def frame_from_bgr(data_bgr, timestamp):
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    return VideoFrame(
        width=data_bgr.shape[1],
        height=data_bgr.shape[0],
        timestamp=timestamp,
        data=data_yuv.tobytes())


def frame_from_gray(data_gray, timestamp):
    data_bgr = cv2.cvtColor(data_gray, cv2.COLOR_GRAY2BGR)
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    return VideoFrame(
        width=data_bgr.shape[1],
        height=data_bgr.shape[0],
        timestamp=timestamp,
        data=data_yuv.tobytes())


def frame_to_bgr(frame):
    data_flat = numpy.frombuffer(frame.data, numpy.uint8)
    data_yuv = data_flat.reshape((math.ceil(frame.height * 12 / 8), frame.width))
    return cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_I420)


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
                    frame = audio_frame_from_avframe(frame)
                    asyncio.run_coroutine_threadsafe(audio_track._queue.put(
                        (frame, frame_time)), loop)
                else:
                    break
        elif isinstance(frame, av.VideoFrame) and video_track:
            if video_track._queue.qsize() < 30:
                frame_time = frame.time
                frame = video_frame_from_avframe(frame)
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


class MediaRecorder:
    def __init__(self, path):
        self.__container = av.open(file=path, mode='w')

        self.__audio_stream = None
        self.__audio_task = None
        self.__audio_track = None

        self.__video_stream = None
        self.__video_task = None
        self.__video_track = None

    def addTrack(self, track):
        if track.kind == 'audio':
            if self.__container.format.name == 'wav':
                codec_name = 'pcm_s16le'
            else:
                codec_name = 'aac'
            self.__audio_stream = self.__container.add_stream(codec_name)
            self.__audio_track = track
        else:
            self.__video_stream = self.__container.add_stream('h264', rate=30)
            self.__video_track = track

    def start(self):
        if self.__audio_track:
            self.__audio_task = asyncio.ensure_future(self.__run_audio())
        if self.__video_track:
            self.__video_task = asyncio.ensure_future(self.__run_video())

    def stop(self):
        if self.__audio_task:
            self.__audio_task.cancel()
            self.__audio_task = None
            for packet in self.__audio_stream.encode(None):
                self.__container.mux(packet)

        if self.__video_task:
            self.__video_task.cancel()
            self.__video_task = None

        if self.__container:
            self.__container.close()
            self.__container = None

    async def __run_audio(self):
        while True:
            frame = await self.__audio_track.recv()
            avframe = audio_frame_to_avframe(frame)
            for packet in self.__audio_stream.encode(avframe):
                self.__container.mux(packet)

    async def __run_video(self):
        while True:
            frame = await self.__video_track.recv()
            avframe = video_frame_to_avframe(frame)
            for packet in self.__video_stream.encode(avframe):
                self.__container.mux(packet)
