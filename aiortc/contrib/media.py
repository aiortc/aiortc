import asyncio
import fractions
import threading
import time

import av
import av.audio.stream
import av.video.stream
import numpy

from ..mediastreams import (AUDIO_PTIME, VIDEO_TIME_BASE, AudioFrame,
                            MediaStreamTrack, VideoFrame)


def audio_frame_from_avframe(av_frame):
    """
    Convert an av.AudioFrame to aiortc.AudioFrame.
    """
    assert av_frame.format.name == 's16'
    assert len(av_frame.planes) == 1
    frame = AudioFrame(
        channels=len(av_frame.layout.channels),
        data=av_frame.planes[0].to_bytes(),
        sample_rate=av_frame.sample_rate)
    frame.pts = av_frame.pts
    frame.time_base = av_frame.time_base
    return frame


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
    av_frame.time_base = frame.time_base
    return av_frame


def video_frame_from_avframe(av_frame):
    """
    Convert an av.VideoFrame to aiortc.VideoFrame.
    """
    if str(av_frame.format) != 'yuv420p':
        av_frame = av_frame.reformat(format='yuv420p')

    data = b''
    shifts = [0, 1, 1]
    for i, plane in enumerate(av_frame.planes):
        arr = numpy.frombuffer(plane, numpy.uint8).reshape(-1, plane.line_size)
        data += bytes(arr[
            :(av_frame.height >> shifts[i]),
            :(av_frame.width >> shifts[i]),
        ])

    frame = VideoFrame(
        width=av_frame.width,
        height=av_frame.height,
        data=data)
    frame.pts = av_frame.pts
    frame.time_base = av_frame.time_base
    return frame


def video_frame_to_avframe(frame):
    """
    Convert an aiortc.VideoFrame to av.VideoFrame.
    """
    u_start = frame.width * frame.height
    v_start = 5 * u_start // 4
    av_frame = av.VideoFrame(frame.width, frame.height, 'yuv420p')
    assert av_frame.planes[0].line_size == av_frame.width
    av_frame.planes[0].update(frame.data[0:u_start])
    av_frame.planes[1].update(frame.data[u_start:v_start])
    av_frame.planes[2].update(frame.data[v_start:])
    av_frame.pts = frame.pts
    av_frame.time_base = frame.time_base
    return av_frame


def video_frame_from_bgr(data_bgr, timestamp):
    import cv2
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_I420)
    frame = VideoFrame(
        width=data_bgr.shape[1],
        height=data_bgr.shape[0],
        data=data_yuv.tobytes())
    frame.pts = timestamp
    frame.time_base = VIDEO_TIME_BASE
    return frame


def video_frame_to_bgr(frame):
    import cv2
    data_yuv = numpy.frombuffer(frame.data, numpy.uint8).reshape(-1, frame.width)
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
        """
        Add a track whose media should be discarded.

        :param: track: An :class:`aiortc.AudioStreamTrack` or :class:`aiortc.VideoStreamTrack`.
        """
        if track not in self.__tracks:
            self.__tracks[track] = None

    def removeTrack(self, track):
        if track in self.__tracks:
            task = self.__tracks.pop(track)
            if task is not None:
                task.cancel()

    def start(self):
        """
        Start discarding media.
        """
        for track, task in self.__tracks.items():
            if task is None:
                self.__tracks[track] = asyncio.ensure_future(blackhole_consume(track))

    def stop(self):
        """
        Stop discarding media.
        """
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

    video_first_pts = None

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

        # read up to 1 second ahead
        elapsed_time = (time.time() - start_time)
        if frame_time and frame_time > elapsed_time + 1:
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
                    asyncio.run_coroutine_threadsafe(audio_track._queue.put(frame), loop)
                else:
                    break
        elif isinstance(frame, av.VideoFrame) and video_track:
            # video from a webcam doesn't start at pts 0, cancel out offset
            if frame.pts is not None:
                if video_first_pts is None:
                    video_first_pts = frame.pts
                frame.pts -= video_first_pts

            frame_time = frame.time
            frame = video_frame_from_avframe(frame)
            asyncio.run_coroutine_threadsafe(video_track._queue.put(frame), loop)


class PlayerStreamTrack(MediaStreamTrack):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self._ended = False
        self._queue = asyncio.Queue()
        self._start = None

    async def recv(self):
        frame = await self._queue.get()
        frame_time = frame.time

        # control playback rate
        if frame_time is not None:
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
    A media source that reads audio and/or video from a file.
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
                self.__audio = PlayerStreamTrack(kind='audio')
            elif isinstance(stream, av.video.stream.VideoStream) and not self.__video:
                self.__video = PlayerStreamTrack(kind='video')

    @property
    def audio(self):
        """
        An :class:`aiortc.AudioStreamTrack` instance if the file contains audio.
        """
        return self.__audio

    @property
    def video(self):
        """
        A :class:`aiortc.VideoStreamTrack` instance if the file contains video.
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
    A media sink that writes audio and/or video to a file.
    """
    def __init__(self, path):
        self.__container = av.open(file=path, mode='w')
        self.__tracks = {}

    def addTrack(self, track):
        """
        Add a track to be recorded.

        :param: track: An :class:`aiortc.AudioStreamTrack` or :class:`aiortc.VideoStreamTrack`.
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
                stream = self.__container.add_stream('libx264', rate=30)
                stream.pix_fmt = 'yuv420p'
            stream.time_base = VIDEO_TIME_BASE
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
                    for packet in context.stream.encode(None):
                        # FIXME : for some reason these "flush" packets do not have
                        # their time_base set, so let's fix this
                        packet.time_base = context.stream.time_base
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
