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


def video_frame_from_bgr(data_bgr, timestamp):
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    return VideoFrame(
        width=data_bgr.shape[1],
        height=data_bgr.shape[0],
        timestamp=timestamp,
        data=data_yuv.tobytes())


def video_frame_from_gray(data_gray, timestamp):
    data_bgr = cv2.cvtColor(data_gray, cv2.COLOR_GRAY2BGR)
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    return VideoFrame(
        width=data_bgr.shape[1],
        height=data_bgr.shape[0],
        timestamp=timestamp,
        data=data_yuv.tobytes())


def video_frame_to_bgr(frame):
    data_flat = numpy.frombuffer(frame.data, numpy.uint8)
    data_yuv = data_flat.reshape((math.ceil(frame.height * 12 / 8), frame.width))
    return cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_I420)


async def blackhole_consume(track):
    while True:
        await track.recv()


class MediaBlackhole:
    """
    A media sink that consumes and discards all media.
    """
    def __init__(self):
        self.__tracks = {}

    def addTrack(self, track):
        if track not in self.__tracks:
            self.__tracks[track] = None

    def removeTrack(self, track):
        if track in self.__tracks:
            task = self.__tracks.pop(track)
            if task is not None:
                task.cancel()

    def start(self):
        for track, task in self.__tracks.items():
            if task is None:
                self.__tracks[track] = asyncio.ensure_future(blackhole_consume(track))

    def stop(self):
        for task in self.__tracks.values():
            if task is not None:
                task.cancel()
        self.__tracks = {}


def player_worker(loop, container, audio_track, video_track, quit_event):
    audio_fifo = av.audio.fifo.AudioFifo()
    audio_format = av.audio.format.AudioFormat('s16')
    audio_sample_rate = 48000
    audio_samples = 0
    audio_samples_per_frame = int(audio_sample_rate * AUDIO_PTIME)
    audio_resampler = av.audio.resampler.AudioResampler(
        format=audio_format,
        rate=audio_sample_rate)

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
            if frame.format != audio_format or frame.sample_rate != audio_sample_rate:
                frame.pts = None
                frame = audio_resampler.resample(frame)

            # fix timestamps
            frame.pts = audio_samples
            frame.time_base = fractions.Fraction(1, audio_sample_rate)
            audio_samples += frame.samples

            audio_fifo.write(frame)
            while True:
                frame = audio_fifo.read(audio_samples_per_frame)
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

    def start(self):
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


class MediaRecorderContext:
    def __init__(self, stream, convert):
        self.stream = stream
        self.convert = convert
        self.task = None


class MediaRecorder:
    """
    Allows you to write audio and/or video to a file.
    """
    def __init__(self, path):
        self.__container = av.open(file=path, mode='w')
        self.__tracks = {}

    def addTrack(self, track):
        """
        Add a track to be recorded.

        :param: track: An :class:`AudioStreamTrack` or :class:`VideoStreamTrack`.
        """
        if track.kind == 'audio':
            convert = audio_frame_to_avframe
            if self.__container.format.name == 'wav':
                codec_name = 'pcm_s16le'
            elif self.__container.format.name == 'mp3':
                codec_name = 'mp3'
            else:
                codec_name = 'aac'
            stream = self.__container.add_stream(codec_name)
        else:
            convert = video_frame_to_avframe
            if self.__container.format.name == 'image2':
                stream = self.__container.add_stream('jpeg2000', rate=30)
                stream.pix_fmt = 'rgb24'
            else:
                stream = self.__container.add_stream('h264', rate=30)
                stream.pix_fmt = 'yuv420p'
        self.__tracks[track] = MediaRecorderContext(stream, convert)

    def start(self):
        """
        Start recording.
        """
        for track, context in self.__tracks.items():
            if context.task is None:
                context.task = asyncio.ensure_future(self.__run_track(track, context))

    def stop(self):
        """
        Stop recording.
        """
        if self.__container:
            for track, context in self.__tracks.items():
                if context.task is not None:
                    context.task.cancel()
                    context.task = None

                    if track.kind == 'audio':  # FIXME: do this for video too
                        for packet in context.stream.encode(None):
                            self.__container.mux(packet)
            self.__tracks = {}

            if self.__container:
                self.__container.close()
                self.__container = None

    async def __run_track(self, track, context):
        while True:
            frame = await track.recv()
            avframe = context.convert(frame)
            for packet in context.stream.encode(avframe):
                self.__container.mux(packet)
