import asyncio
import fractions
import logging
import threading
import time

import av
from av import AudioFrame, VideoFrame

from ..mediastreams import AUDIO_PTIME, MediaStreamError, MediaStreamTrack

logger = logging.getLogger('media')


async def blackhole_consume(track):
    while True:
        try:
            await track.recv()
        except MediaStreamError:
            return


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

    async def start(self):
        """
        Start discarding media.
        """
        for track, task in self.__tracks.items():
            if task is None:
                self.__tracks[track] = asyncio.ensure_future(blackhole_consume(track))

    async def stop(self):
        """
        Stop discarding media.
        """
        for task in self.__tracks.values():
            if task is not None:
                task.cancel()
        self.__tracks = {}


def player_worker(loop, container, audio_track, video_track, quit_event, throttle_playback):
    import av.audio.fifo
    import av.audio.format
    import av.audio.resampler
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
        except (av.AVError, StopIteration):
            if audio_track:
                asyncio.run_coroutine_threadsafe(audio_track._queue.put(None), loop)
            if video_track:
                asyncio.run_coroutine_threadsafe(video_track._queue.put(None), loop)
            break

        # read up to 1 second ahead
        if throttle_playback:
            elapsed_time = (time.time() - start_time)
            if frame_time and frame_time > elapsed_time + 1:
                time.sleep(0.1)

        if isinstance(frame, AudioFrame) and audio_track:
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
                    asyncio.run_coroutine_threadsafe(audio_track._queue.put(frame), loop)
                else:
                    break
        elif isinstance(frame, VideoFrame) and video_track:
            # video from a webcam doesn't start at pts 0, cancel out offset
            if frame.pts is not None:
                if video_first_pts is None:
                    video_first_pts = frame.pts
                frame.pts -= video_first_pts

            frame_time = frame.time
            asyncio.run_coroutine_threadsafe(video_track._queue.put(frame), loop)


class PlayerStreamTrack(MediaStreamTrack):
    def __init__(self, player, kind):
        super().__init__()
        self.kind = kind
        self._player = player
        self._queue = asyncio.Queue()
        self._start = None

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError

        self._player._start(self)
        frame = await self._queue.get()
        if frame is None:
            self.stop()
            raise MediaStreamError
        frame_time = frame.time

        # control playback rate
        if self._player._throttle_playback and frame_time is not None:
            if self._start is None:
                self._start = time.time() - frame_time
            else:
                wait = self._start + frame_time - time.time()
                await asyncio.sleep(wait)

        return frame

    def stop(self):
        super().stop()
        if self._player is not None:
            self._player._stop(self)
            self._player = None


class MediaPlayer:
    """
    A media source that reads audio and/or video from a file.

    Examples:

    .. code-block:: python

        # Open a video file.
        player = MediaPlayer('/path/to/some.mp4')

        # Open an HTTP stream.
        player = MediaPlayer(
            'http://download.tsi.telecom-paristech.fr/'
            'gpac/dataset/dash/uhd/mux_sources/hevcds_720p30_2M.mp4')

        # Open webcam on Linux.
        player = MediaPlayer('/dev/video0', options={
            'video_size': 'vga'
        })

    :param: file: The path to a file, or a file-like object.
    :param: format: The format to use, defaults to autodect.
    :param: options: Additional options to pass to FFmpeg.
    """
    def __init__(self, file, format=None, options={}):
        import av.audio.stream
        import av.video.stream
        self.__container = av.open(file=file, format=format, mode='r', options=options)
        self.__thread = None
        self.__thread_quit = None

        # examine streams
        self.__started = set()
        self.__audio = None
        self.__video = None
        for stream in self.__container.streams:
            if isinstance(stream, av.audio.stream.AudioStream) and not self.__audio:
                self.__audio = PlayerStreamTrack(self, kind='audio')
            elif isinstance(stream, av.video.stream.VideoStream) and not self.__video:
                self.__video = PlayerStreamTrack(self, kind='video')

        # check whether we need to throttle playback
        container_format = set(self.__container.format.name.split(','))
        self._throttle_playback = not container_format.intersection([
            'avfoundation', 'dshow', 'v4l2', 'vfwcap'])

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

    def _start(self, track):
        self.__started.add(track)
        if self.__thread is None:
            self.__log_debug('Starting worker thread')
            self.__thread_quit = threading.Event()
            self.__thread = threading.Thread(
                name='media-player',
                target=player_worker,
                args=(
                    asyncio.get_event_loop(), self.__container,
                    self.__audio, self.__video,
                    self.__thread_quit,
                    self._throttle_playback))
            self.__thread.start()

    def _stop(self, track):
        self.__started.discard(track)
        if not self.__started and self.__thread is not None:
            self.__log_debug('Stopping worker thread')
            self.__thread_quit.set()
            self.__thread.join()
            self.__thread = None

    def __log_debug(self, msg, *args):
        logger.debug('player(%s) ' + msg, self.__container.name, *args)


class MediaRecorderContext:
    def __init__(self, stream):
        self.stream = stream
        self.task = None


class MediaRecorder:
    """
    A media sink that writes audio and/or video to a file.

    Examples:

    .. code-block:: python

        # Write to a video file.
        player = MediaRecorder('/path/to/file.mp4')

        # Write to a set of images.
        player = MediaRecorder('/path/to/file-%3d.png')

    :param: file: The path to a file, or a file-like object.
    :param: format: The format to use, defaults to autodect.
    :param: options: Additional options to pass to FFmpeg.
    """
    def __init__(self, file, format=None, options={}):
        self.__container = av.open(file=file, format=format, mode='w', options=options)
        self.__tracks = {}

    def addTrack(self, track):
        """
        Add a track to be recorded.

        :param: track: An :class:`aiortc.AudioStreamTrack` or :class:`aiortc.VideoStreamTrack`.
        """
        if track.kind == 'audio':
            if self.__container.format.name == 'wav':
                codec_name = 'pcm_s16le'
            elif self.__container.format.name == 'mp3':
                codec_name = 'mp3'
            else:
                codec_name = 'aac'
            stream = self.__container.add_stream(codec_name)
        else:
            if self.__container.format.name == 'image2':
                stream = self.__container.add_stream('png', rate=30)
                stream.pix_fmt = 'rgb24'
            else:
                stream = self.__container.add_stream('libx264', rate=30)
                stream.pix_fmt = 'yuv420p'
        self.__tracks[track] = MediaRecorderContext(stream)

    async def start(self):
        """
        Start recording.
        """
        for track, context in self.__tracks.items():
            if context.task is None:
                context.task = asyncio.ensure_future(self.__run_track(track, context))

    async def stop(self):
        """
        Stop recording.
        """
        if self.__container:
            for track, context in self.__tracks.items():
                if context.task is not None:
                    context.task.cancel()
                    context.task = None
                    for packet in context.stream.encode(None):
                        self.__container.mux(packet)
            self.__tracks = {}

            if self.__container:
                self.__container.close()
                self.__container = None

    async def __run_track(self, track, context):
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            for packet in context.stream.encode(frame):
                self.__container.mux(packet)
