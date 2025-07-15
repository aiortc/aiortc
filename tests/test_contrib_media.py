import asyncio
import errno
import os
import tempfile
import time
import wave
from collections.abc import Iterator
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import av
import av.container
import av.stream
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder, MediaRelay
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError, VideoStreamTrack

from .codecs import CodecTestCase
from .utils import asynctest


def get_stream_duration(stream: av.stream.Stream) -> float:
    """
    Return the stream's duration is seconds.
    """
    # For WebM containers, the duration is not set on the stream,
    # we need to check the metadata instead.
    if stream.duration is None:
        duration_str = stream.metadata.get("DURATION", "0")
        # Parse 'HH:MM:SS.sssssssss' format to seconds.
        try:
            h, m, s = duration_str.split(":")
            return float(h) * 3600 + float(m) * 60 + float(s)
        except Exception:
            return 0.0
    else:
        return float(stream.duration * stream.time_base)


class VideoStreamTrackUhd(VideoStreamTrack):
    async def recv(self) -> av.VideoFrame:
        pts, time_base = await self.next_timestamp()

        frame = av.VideoFrame(width=3840, height=2160)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.time_base = time_base
        return frame


class MediaTestCase(CodecTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def create_audio_file(
        self,
        name: str,
        channels: int = 1,
        sample_rate: int = 8000,
        sample_width: int = 2,
    ) -> str:
        path = self.temporary_path(name)

        writer = wave.open(path, "wb")
        writer.setnchannels(channels)
        writer.setframerate(sample_rate)
        writer.setsampwidth(sample_width)

        writer.writeframes(b"\x00" * sample_rate * sample_width * channels)
        writer.close()

        return path

    def create_audio_and_video_file(
        self,
        name: str,
        width: int = 640,
        height: int = 480,
        video_rate: int = 30,
        duration: int = 1,
    ) -> str:
        path = self.temporary_path(name)
        audio_pts = 0
        audio_rate = 48000
        audio_samples = audio_rate // video_rate

        container = av.open(path, "w")
        audio_stream = container.add_stream("libopus", rate=audio_rate)
        video_stream = container.add_stream("h264", rate=video_rate)
        for video_frame in self.create_video_frames(
            width=width, height=height, count=duration * video_rate
        ):
            audio_frame = self.create_audio_frame(
                samples=audio_samples, pts=audio_pts, sample_rate=audio_rate
            )
            audio_pts += audio_samples
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)

            for packet in video_stream.encode(video_frame):
                container.mux(packet)

        for packet in audio_stream.encode(None):
            container.mux(packet)
        for packet in video_stream.encode(None):
            container.mux(packet)
        container.close()

        return path

    def create_video_file(
        self,
        name: str,
        width: int = 640,
        height: int = 480,
        rate: int = 30,
        duration: int = 1,
    ) -> str:
        path = self.temporary_path(name)

        container = av.open(path, "w")
        if name.endswith(".png"):
            stream = container.add_stream("png", rate=rate)
            stream.pix_fmt = "rgb24"
        elif name.endswith(".ts"):
            stream = container.add_stream("h264", rate=rate)
        elif name.endswith(".webm"):
            stream = container.add_stream("libvpx", rate=rate)
            stream.pix_fmt = "yuv420p"
        else:
            assert name.endswith(".mp4")
            stream = container.add_stream("mpeg4", rate=rate)
        for frame in self.create_video_frames(
            width=width, height=height, count=duration * rate
        ):
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()

        return path

    def temporary_path(self, name: str) -> str:
        return os.path.join(self.directory.name, name)


class MediaBlackholeTest(TestCase):
    @asynctest
    async def test_audio(self) -> None:
        recorder = MediaBlackhole()
        recorder.addTrack(AudioStreamTrack())
        await recorder.start()
        await asyncio.sleep(1)
        await recorder.stop()

    @asynctest
    async def test_audio_ended(self) -> None:
        track = AudioStreamTrack()

        recorder = MediaBlackhole()
        recorder.addTrack(track)
        await recorder.start()
        await asyncio.sleep(1)
        track.stop()
        await asyncio.sleep(1)

        await recorder.stop()

    @asynctest
    async def test_audio_and_video(self) -> None:
        recorder = MediaBlackhole()
        recorder.addTrack(AudioStreamTrack())
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

    @asynctest
    async def test_video(self) -> None:
        recorder = MediaBlackhole()
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

    @asynctest
    async def test_video_ended(self) -> None:
        track = VideoStreamTrack()

        recorder = MediaBlackhole()
        recorder.addTrack(track)
        await recorder.start()
        await asyncio.sleep(1)
        track.stop()
        await asyncio.sleep(1)

        await recorder.stop()


class MediaRelayTest(MediaTestCase):
    @asynctest
    async def test_audio_stop_consumer(self) -> None:
        source = AudioStreamTrack()
        relay = MediaRelay()
        proxy1 = relay.subscribe(source)
        proxy2 = relay.subscribe(source)

        # read some frames
        samples_per_frame = 160
        for pts in range(0, 2 * samples_per_frame, samples_per_frame):
            frame1, frame2 = await asyncio.gather(proxy1.recv(), proxy2.recv())

            assert isinstance(frame1, av.AudioFrame)
            self.assertEqual(frame1.format.name, "s16")
            self.assertEqual(frame1.layout.name, "mono")
            self.assertEqual(frame1.pts, pts)
            self.assertEqual(frame1.samples, samples_per_frame)

            assert isinstance(frame2, av.AudioFrame)
            self.assertEqual(frame2.format.name, "s16")
            self.assertEqual(frame2.layout.name, "mono")
            self.assertEqual(frame2.pts, pts)
            self.assertEqual(frame2.samples, samples_per_frame)

        # stop a consumer
        proxy1.stop()

        # continue reading
        for i in range(2):
            exc1, frame3 = await asyncio.gather(
                proxy1.recv(), proxy2.recv(), return_exceptions=True
            )
            self.assertIsInstance(exc1, MediaStreamError)
            self.assertIsInstance(frame3, av.AudioFrame)

        # stop source track
        source.stop()

    @asynctest
    async def test_audio_stop_consumer_unbuffered(self) -> None:
        source = AudioStreamTrack()
        relay = MediaRelay()
        proxy1 = relay.subscribe(source, buffered=False)
        proxy2 = relay.subscribe(source, buffered=False)

        # read some frames
        samples_per_frame = 160
        for pts in range(0, 2 * samples_per_frame, samples_per_frame):
            frame1, frame2 = await asyncio.gather(proxy1.recv(), proxy2.recv())

            assert isinstance(frame1, av.AudioFrame)
            self.assertEqual(frame1.format.name, "s16")
            self.assertEqual(frame1.layout.name, "mono")
            self.assertEqual(frame1.pts, pts)
            self.assertEqual(frame1.samples, samples_per_frame)

            assert isinstance(frame2, av.AudioFrame)
            self.assertEqual(frame2.format.name, "s16")
            self.assertEqual(frame2.layout.name, "mono")
            self.assertEqual(frame2.pts, pts)
            self.assertEqual(frame2.samples, samples_per_frame)

        # stop a consumer
        proxy1.stop()

        # continue reading
        for i in range(2):
            exc1, frame3 = await asyncio.gather(
                proxy1.recv(), proxy2.recv(), return_exceptions=True
            )
            self.assertIsInstance(exc1, MediaStreamError)
            self.assertIsInstance(frame3, av.AudioFrame)

        # stop source track
        source.stop()

    @asynctest
    async def test_audio_stop_source(self) -> None:
        source = AudioStreamTrack()
        relay = MediaRelay()
        proxy1 = relay.subscribe(source)
        proxy2 = relay.subscribe(source)

        # read some frames
        samples_per_frame = 160
        for pts in range(0, 2 * samples_per_frame, samples_per_frame):
            frame1, frame2 = await asyncio.gather(proxy1.recv(), proxy2.recv())

            assert isinstance(frame1, av.AudioFrame)
            self.assertEqual(frame1.format.name, "s16")
            self.assertEqual(frame1.layout.name, "mono")
            self.assertEqual(frame1.pts, pts)
            self.assertEqual(frame1.samples, samples_per_frame)

            assert isinstance(frame2, av.AudioFrame)
            self.assertEqual(frame2.format.name, "s16")
            self.assertEqual(frame2.layout.name, "mono")
            self.assertEqual(frame2.pts, pts)
            self.assertEqual(frame2.samples, samples_per_frame)

        # stop source track
        source.stop()

        # continue reading
        await asyncio.gather(proxy1.recv(), proxy2.recv())
        for i in range(2):
            exc1, exc2 = await asyncio.gather(
                proxy1.recv(), proxy2.recv(), return_exceptions=True
            )
            self.assertIsInstance(exc1, MediaStreamError)
            self.assertIsInstance(exc2, MediaStreamError)

    @asynctest
    async def test_audio_stop_source_unbuffered(self) -> None:
        source = AudioStreamTrack()
        relay = MediaRelay()
        proxy1 = relay.subscribe(source, buffered=False)
        proxy2 = relay.subscribe(source, buffered=False)

        # read some frames
        samples_per_frame = 160
        for pts in range(0, 2 * samples_per_frame, samples_per_frame):
            frame1, frame2 = await asyncio.gather(proxy1.recv(), proxy2.recv())

            assert isinstance(frame1, av.AudioFrame)
            self.assertEqual(frame1.format.name, "s16")
            self.assertEqual(frame1.layout.name, "mono")
            self.assertEqual(frame1.pts, pts)
            self.assertEqual(frame1.samples, samples_per_frame)

            assert isinstance(frame2, av.AudioFrame)
            self.assertEqual(frame2.format.name, "s16")
            self.assertEqual(frame2.layout.name, "mono")
            self.assertEqual(frame2.pts, pts)
            self.assertEqual(frame2.samples, samples_per_frame)

        # stop source track
        source.stop()

        # continue reading
        for i in range(2):
            exc1, exc2 = await asyncio.gather(
                proxy1.recv(), proxy2.recv(), return_exceptions=True
            )
            self.assertIsInstance(exc1, MediaStreamError)
            self.assertIsInstance(exc2, MediaStreamError)

    @asynctest
    async def test_audio_slow_consumer(self) -> None:
        source = AudioStreamTrack()
        relay = MediaRelay()
        proxy1 = relay.subscribe(source, buffered=False)
        proxy2 = relay.subscribe(source, buffered=False)

        # read some frames
        samples_per_frame = 160
        for pts in range(0, 2 * samples_per_frame, samples_per_frame):
            frame1, frame2 = await asyncio.gather(proxy1.recv(), proxy2.recv())

            assert isinstance(frame1, av.AudioFrame)
            self.assertEqual(frame1.format.name, "s16")
            self.assertEqual(frame1.layout.name, "mono")
            self.assertEqual(frame1.pts, pts)
            self.assertEqual(frame1.samples, samples_per_frame)

            assert isinstance(frame2, av.AudioFrame)
            self.assertEqual(frame2.format.name, "s16")
            self.assertEqual(frame2.layout.name, "mono")
            self.assertEqual(frame2.pts, pts)
            self.assertEqual(frame2.samples, samples_per_frame)

        # skip some frames
        timestamp = 5 * samples_per_frame
        await asyncio.sleep(source._start + (timestamp / 8000) - time.time())

        frame1, frame2 = await asyncio.gather(proxy1.recv(), proxy2.recv())

        assert isinstance(frame1, av.AudioFrame)
        self.assertEqual(frame1.format.name, "s16")
        self.assertEqual(frame1.layout.name, "mono")
        self.assertEqual(frame1.pts, 5 * samples_per_frame)
        self.assertEqual(frame1.samples, samples_per_frame)

        assert isinstance(frame2, av.AudioFrame)
        self.assertEqual(frame2.format.name, "s16")
        self.assertEqual(frame2.layout.name, "mono")
        self.assertEqual(frame2.pts, 5 * samples_per_frame)
        self.assertEqual(frame2.samples, samples_per_frame)

        # stop a consumer
        proxy1.stop()

        # continue reading
        for i in range(2):
            exc1, frame3 = await asyncio.gather(
                proxy1.recv(), proxy2.recv(), return_exceptions=True
            )
            self.assertIsInstance(exc1, MediaStreamError)
            self.assertIsInstance(frame3, av.AudioFrame)

        # stop source track
        source.stop()


class BufferingInputContainer:
    def __init__(self, real: av.container.InputContainer) -> None:
        self.__failed = False
        self.__real = real

    def decode(self, *args: Any) -> Iterator[Any]:
        # fail with EAGAIN once
        if not self.__failed:
            self.__failed = True
            raise av.FFmpegError(errno.EAGAIN, "EAGAIN")

        return self.__real.decode(*args)

    def demux(self, *args: Any) -> Iterator[Any]:
        # fail with EAGAIN once
        if not self.__failed:
            self.__failed = True
            raise av.FFmpegError(errno.EAGAIN, "EAGAIN")

        return self.__real.demux(*args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__real, name)


class MediaPlayerTest(MediaTestCase):
    def assertAudio(self, frame: Any) -> None:
        assert isinstance(frame, av.AudioFrame)
        self.assertEqual(frame.format.name, "s16")
        self.assertEqual(frame.layout.name, "stereo")
        self.assertEqual(frame.samples, 960)
        self.assertEqual(frame.sample_rate, 48000)

    def assertVideo(self, frame: Any) -> None:
        assert isinstance(frame, av.VideoFrame)
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)

    def createMediaPlayer(self, path: str, loop: bool = False) -> MediaPlayer:
        return MediaPlayer(path, loop=loop)

    def endTime(self, frame: Any) -> float:
        assert isinstance(frame, av.AudioFrame)
        return frame.time + frame.samples / frame.sample_rate

    @asynctest
    async def test_audio_file_8kHz(self) -> None:
        path = self.create_audio_file("test.wav")
        player = self.createMediaPlayer(path)

        if isinstance(self, MediaPlayerNoDecodeTest):
            self.assertIsNone(player.audio)
            self.assertIsNone(player.video)
        else:
            # check tracks
            self.assertIsNotNone(player.audio)
            self.assertIsNone(player.video)

            # read all frames
            self.assertEqual(player.audio.readyState, "live")
            while True:
                frame = await player.audio.recv()
                self.assertAudio(frame)
                if self.endTime(frame) >= 0.98:
                    break
            with self.assertRaises(MediaStreamError):
                await player.audio.recv()
            self.assertEqual(player.audio.readyState, "ended")

            # try reading again
            with self.assertRaises(MediaStreamError):
                await player.audio.recv()

    @asynctest
    async def test_audio_file_48kHz(self) -> None:
        path = self.create_audio_file("test.wav", sample_rate=48000)
        player = self.createMediaPlayer(path)

        if isinstance(self, MediaPlayerNoDecodeTest):
            self.assertIsNone(player.audio)
            self.assertIsNone(player.video)
        else:
            # check tracks
            self.assertIsNotNone(player.audio)
            self.assertIsNone(player.video)

            # read all frames
            self.assertEqual(player.audio.readyState, "live")
            while True:
                frame = await player.audio.recv()
                if self.endTime(frame) >= 1.0:
                    break
                self.assertAudio(frame)
            with self.assertRaises(MediaStreamError):
                await player.audio.recv()
            self.assertEqual(player.audio.readyState, "ended")

    @asynctest
    async def test_audio_file_looping(self) -> None:
        path = self.create_audio_file("test.wav", sample_rate=48000)
        player = self.createMediaPlayer(path, loop=True)

        if isinstance(self, MediaPlayerNoDecodeTest):
            self.assertIsNone(player.audio)
        else:
            # read all frames, then loop and re-read all frames
            self.assertEqual(player.audio.readyState, "live")
            for i in range(100):
                frame = await player.audio.recv()
                self.assertAudio(frame)

            # read one more time, forcing a second loop
            await player.audio.recv()
            self.assertEqual(player.audio.readyState, "live")

            # stop the player
            player.audio.stop()

    @asynctest
    async def test_audio_and_video_file(self) -> None:
        path = self.create_audio_and_video_file(name="test.ts", duration=5)
        player = self.createMediaPlayer(path)

        # check tracks
        self.assertIsNotNone(player.audio)
        self.assertIsNotNone(player.video)

        # read some frames
        self.assertEqual(player.audio.readyState, "live")
        self.assertEqual(player.video.readyState, "live")
        for i in range(10):
            await asyncio.gather(player.audio.recv(), player.video.recv())

        # stop audio track
        player.audio.stop()

        # continue reading
        for i in range(10):
            with self.assertRaises(MediaStreamError):
                await player.audio.recv()
            await player.video.recv()

        # stop video track
        player.video.stop()

        # continue reading
        with self.assertRaises(MediaStreamError):
            await player.audio.recv()
        with self.assertRaises(MediaStreamError):
            await player.video.recv()

    @asynctest
    async def test_video_file_mp4(self) -> None:
        path = self.create_video_file("test.mp4", duration=3)
        player = self.createMediaPlayer(path)

        if isinstance(self, MediaPlayerNoDecodeTest):
            self.assertIsNone(player.audio)
            self.assertIsNone(player.video)
        else:
            # check tracks
            self.assertIsNone(player.audio)
            self.assertIsNotNone(player.video)

            # read all frames
            self.assertEqual(player.video.readyState, "live")
            for i in range(90):
                frame = await player.video.recv()
                self.assertVideo(frame)
            with self.assertRaises(MediaStreamError):
                await player.video.recv()
            self.assertEqual(player.video.readyState, "ended")

    @asynctest
    async def test_audio_and_video_file_mpegts_eagain(self) -> None:
        path = self.create_audio_and_video_file("test.ts", duration=3)
        container = BufferingInputContainer(av.open(path, "r"))

        with patch("av.open") as mock_open:
            mock_open.return_value = container
            player = self.createMediaPlayer(path)

        # check tracks
        self.assertIsNotNone(player.audio)
        self.assertIsNotNone(player.video)

        # read all frames
        self.assertEqual(player.video.readyState, "live")
        error_count = 0
        received_count = 0
        for i in range(100):
            try:
                frame = await player.video.recv()
                self.assertVideo(frame)
                received_count += 1
            except MediaStreamError:
                error_count += 1
                break
        self.assertEqual(error_count, 1)
        self.assertGreaterEqual(received_count, 89)
        self.assertEqual(player.video.readyState, "ended")

    @asynctest
    async def test_video_file_mpegts_looping(self) -> None:
        path = self.create_video_file("test.ts", duration=5)
        player = self.createMediaPlayer(path, loop=True)

        # read all frames, then loop and re-read all frames
        self.assertEqual(player.video.readyState, "live")
        for i in range(100):
            frame = await player.video.recv()
            self.assertVideo(frame)

        # read one more time, forcing a second loop
        await player.video.recv()
        self.assertEqual(player.video.readyState, "live")

        # stop the player
        player.video.stop()

    @asynctest
    async def test_video_file_png(self) -> None:
        path = self.create_video_file("test-%3d.png", duration=3)
        player = self.createMediaPlayer(path)

        if isinstance(self, MediaPlayerNoDecodeTest):
            self.assertIsNone(player.audio)
            self.assertIsNone(player.video)
        else:
            # check tracks
            self.assertIsNone(player.audio)
            self.assertIsNotNone(player.video)

            # read all frames
            self.assertEqual(player.video.readyState, "live")
            for i in range(90):
                frame = await player.video.recv()
                self.assertVideo(frame)
            with self.assertRaises(MediaStreamError):
                await player.video.recv()
            self.assertEqual(player.video.readyState, "ended")

    @asynctest
    async def test_video_file_webm(self) -> None:
        path = self.create_video_file("test.webm", duration=3)
        player = self.createMediaPlayer(path)

        # check tracks
        self.assertIsNone(player.audio)
        self.assertIsNotNone(player.video)

        # read all frames
        self.assertEqual(player.video.readyState, "live")
        for i in range(90):
            frame = await player.video.recv()
            self.assertVideo(frame)
        with self.assertRaises(MediaStreamError):
            await player.video.recv()
        self.assertEqual(player.video.readyState, "ended")


class MediaPlayerNoDecodeTest(MediaPlayerTest):
    def assertAudio(self, packet: Any) -> None:
        self.assertIsInstance(packet, av.Packet)

    def assertVideo(self, packet: Any) -> None:
        self.assertIsInstance(packet, av.Packet)

    def createMediaPlayer(self, path: str, loop: bool = False) -> MediaPlayer:
        return MediaPlayer(path, decode=False, loop=loop)

    def endTime(self, packet: Any) -> float:
        assert isinstance(packet, av.Packet)
        return float((packet.pts + packet.duration) * packet.time_base)


class MediaRecorderTest(MediaTestCase):
    def assertAudioStream(self, stream: av.stream.Stream, codec_name: str) -> None:
        assert isinstance(stream, av.AudioStream)
        self.assertEqual(stream.codec.name, codec_name)

        self.assertGreater(get_stream_duration(stream), 0)

    def assertVideoStream(
        self,
        stream: av.stream.Stream,
        codec_name: str,
        width: int = 640,
        height: int = 480,
    ) -> None:
        assert isinstance(stream, av.VideoStream)
        self.assertEqual(stream.codec.name, codec_name)

        self.assertGreater(get_stream_duration(stream), 0)

        self.assertEqual(stream.width, width)
        self.assertEqual(stream.height, height)

    async def check_audio_recording(self, filename: str, codec_names: set[str]) -> None:
        # Record audio.
        path = self.temporary_path(filename)
        recorder = MediaRecorder(path)
        recorder.addTrack(AudioStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # Check audio recording.
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 1)
            self.assertIn(container.streams[0].codec.name, codec_names)
            self.assertGreater(get_stream_duration(container.streams[0]), 0)

    @asynctest
    async def test_audio_mp3(self) -> None:
        await self.check_audio_recording("test.mp3", {"mp3", "mp3float"})

    @asynctest
    async def test_audio_ogg(self) -> None:
        await self.check_audio_recording("test.ogg", {"opus"})

    @asynctest
    async def test_audio_wav(self) -> None:
        await self.check_audio_recording("test.wav", {"pcm_s16le"})

    @asynctest
    async def test_audio_wav_ended(self) -> None:
        track = AudioStreamTrack()

        recorder = MediaRecorder(self.temporary_path("test.wav"))
        recorder.addTrack(track)
        await recorder.start()
        await asyncio.sleep(1)
        track.stop()
        await asyncio.sleep(1)

        await recorder.stop()

    @asynctest
    async def test_audio_webm(self) -> None:
        await self.check_audio_recording("test.webm", {"opus"})

    @asynctest
    async def test_audio_and_video_mp4(self) -> None:
        path = self.temporary_path("test.mp4")
        recorder = MediaRecorder(path)
        recorder.addTrack(AudioStreamTrack())
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # check output media
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 2)

            self.assertAudioStream(container.streams[0], "aac")
            self.assertVideoStream(container.streams[1], "h264")

    @asynctest
    async def test_audio_and_video_webm(self) -> None:
        path = self.temporary_path("test.webm")
        recorder = MediaRecorder(path)
        recorder.addTrack(AudioStreamTrack())
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # check output media
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 2)

            self.assertAudioStream(container.streams[0], "opus")
            self.assertVideoStream(container.streams[1], "vp8")

    @asynctest
    async def test_video_png(self) -> None:
        path = self.temporary_path("test-%3d.png")
        recorder = MediaRecorder(path)
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # check output media
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 1)
            self.assertVideoStream(container.streams[0], "png")

    @asynctest
    async def test_video_mp4(self) -> None:
        path = self.temporary_path("test.mp4")
        recorder = MediaRecorder(path)
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # check output media
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 1)
            self.assertVideoStream(container.streams[0], "h264")

    @asynctest
    async def test_video_mp4_uhd(self) -> None:
        path = self.temporary_path("test.mp4")
        recorder = MediaRecorder(path)
        recorder.addTrack(VideoStreamTrackUhd())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # check output media
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 1)
            self.assertVideoStream(
                container.streams[0], "h264", width=3840, height=2160
            )

    @asynctest
    async def test_video_webm(self) -> None:
        path = self.temporary_path("test.webm")
        recorder = MediaRecorder(path)
        recorder.addTrack(VideoStreamTrack())
        await recorder.start()
        await asyncio.sleep(2)
        await recorder.stop()

        # check output media
        with av.open(path, "r") as container:
            self.assertEqual(len(container.streams), 1)
            self.assertVideoStream(container.streams[0], "vp8")
