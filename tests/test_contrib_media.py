import asyncio
import os
import tempfile
import wave
from unittest import TestCase

import av

from aiortc import AudioStreamTrack, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder
from aiortc.mediastreams import MediaStreamError

from .codecs import CodecTestCase
from .utils import run


class MediaTestCase(CodecTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def create_audio_file(self, name, channels=1, sample_rate=8000, sample_width=2):
        path = self.temporary_path(name)

        writer = wave.open(path, 'wb')
        writer.setnchannels(channels)
        writer.setframerate(sample_rate)
        writer.setsampwidth(sample_width)

        writer.writeframes(b'\x00' * sample_rate * sample_width * channels)
        writer.close()

        return path

    def create_video_file(self, name, width=640, height=480, rate=30, duration=1):
        path = self.temporary_path(name)

        container = av.open(path, 'w')
        if name.endswith('.png'):
            stream = container.add_stream('png', rate=rate)
            stream.pix_fmt = 'rgb24'
        else:
            stream = container.add_stream('mpeg4', rate=rate)
        for frame in self.create_video_frames(width=width, height=height, count=duration * rate):
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()

        return path

    def temporary_path(self, name):
        return os.path.join(self.directory.name, name)


class MediaBlackholeTest(TestCase):
    def test_audio(self):
        recorder = MediaBlackhole()
        recorder.addTrack(AudioStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(1))
        run(recorder.stop())

    def test_audio_ended(self):
        track = AudioStreamTrack()

        recorder = MediaBlackhole()
        recorder.addTrack(track)
        run(recorder.start())
        run(asyncio.sleep(1))
        track.stop()
        run(asyncio.sleep(1))

        run(recorder.stop())

    def test_audio_and_video(self):
        recorder = MediaBlackhole()
        recorder.addTrack(AudioStreamTrack())
        recorder.addTrack(VideoStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

    def test_video(self):
        recorder = MediaBlackhole()
        recorder.addTrack(VideoStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

    def test_video_ended(self):
        track = VideoStreamTrack()

        recorder = MediaBlackhole()
        recorder.addTrack(track)
        run(recorder.start())
        run(asyncio.sleep(1))
        track.stop()
        run(asyncio.sleep(1))

        run(recorder.stop())


class MediaPlayerTest(MediaTestCase):
    def test_audio_file_8kHz(self):
        path = self.create_audio_file('test.wav')
        player = MediaPlayer(path)

        # check tracks
        self.assertIsNotNone(player.audio)
        self.assertIsNone(player.video)

        # read all frames
        self.assertEqual(player.audio.readyState, 'live')
        for i in range(49):
            frame = run(player.audio.recv())
            self.assertEqual(frame.format.name, 's16')
            self.assertEqual(frame.layout.name, 'mono')
            self.assertEqual(frame.samples, 960)
            self.assertEqual(frame.sample_rate, 48000)
        with self.assertRaises(MediaStreamError):
            run(player.audio.recv())
        self.assertEqual(player.audio.readyState, 'ended')

        # try reading again
        with self.assertRaises(MediaStreamError):
            run(player.audio.recv())

    def test_audio_file_48kHz(self):
        path = self.create_audio_file('test.wav', sample_rate=48000)
        player = MediaPlayer(path)

        # check tracks
        self.assertIsNotNone(player.audio)
        self.assertIsNone(player.video)

        # read all frames
        self.assertEqual(player.audio.readyState, 'live')
        for i in range(50):
            frame = run(player.audio.recv())
            self.assertEqual(frame.format.name, 's16')
            self.assertEqual(frame.layout.name, 'mono')
            self.assertEqual(frame.samples, 960)
            self.assertEqual(frame.sample_rate, 48000)
        with self.assertRaises(MediaStreamError):
            run(player.audio.recv())
        self.assertEqual(player.audio.readyState, 'ended')

    def test_video_file_png(self):
        path = self.create_video_file('test-%3d.png', duration=3)
        player = MediaPlayer(path)

        # check tracks
        self.assertIsNone(player.audio)
        self.assertIsNotNone(player.video)

        # read all frames
        self.assertEqual(player.video.readyState, 'live')
        for i in range(90):
            frame = run(player.video.recv())
            self.assertEqual(frame.width, 640)
            self.assertEqual(frame.height, 480)
        with self.assertRaises(MediaStreamError):
            run(player.video.recv())
        self.assertEqual(player.video.readyState, 'ended')

    def test_video_file_mp4(self):
        path = self.create_video_file('test.mp4', duration=3)
        player = MediaPlayer(path)

        # check tracks
        self.assertIsNone(player.audio)
        self.assertIsNotNone(player.video)

        # read all frames
        self.assertEqual(player.video.readyState, 'live')
        for i in range(90):
            frame = run(player.video.recv())
            self.assertEqual(frame.width, 640)
            self.assertEqual(frame.height, 480)
        with self.assertRaises(MediaStreamError):
            run(player.video.recv())
        self.assertEqual(player.video.readyState, 'ended')


class MediaRecorderTest(MediaTestCase):
    def test_audio_mp3(self):
        path = self.temporary_path('test.mp3')
        recorder = MediaRecorder(path)
        recorder.addTrack(AudioStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

        # check output media
        container = av.open(path, 'r')
        self.assertEqual(len(container.streams), 1)
        self.assertIn(container.streams[0].codec.name, ('mp3', 'mp3float'))
        self.assertGreater(
            float(container.streams[0].duration * container.streams[0].time_base), 0)

    def test_audio_wav(self):
        path = self.temporary_path('test.wav')
        recorder = MediaRecorder(path)
        recorder.addTrack(AudioStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

        # check output media
        container = av.open(path, 'r')
        self.assertEqual(len(container.streams), 1)
        self.assertEqual(container.streams[0].codec.name, 'pcm_s16le')
        self.assertGreater(
            float(container.streams[0].duration * container.streams[0].time_base), 0)

    def test_audio_wav_ended(self):
        track = AudioStreamTrack()

        recorder = MediaRecorder(self.temporary_path('test.wav'))
        recorder.addTrack(track)
        run(recorder.start())
        run(asyncio.sleep(1))
        track.stop()
        run(asyncio.sleep(1))

        run(recorder.stop())

    def test_audio_and_video(self):
        path = self.temporary_path('test.mp4')
        recorder = MediaRecorder(path)
        recorder.addTrack(AudioStreamTrack())
        recorder.addTrack(VideoStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

        # check output media
        container = av.open(path, 'r')
        self.assertEqual(len(container.streams), 2)

        self.assertEqual(container.streams[0].codec.name, 'aac')
        self.assertGreater(
            float(container.streams[0].duration * container.streams[0].time_base), 0)

        self.assertEqual(container.streams[1].codec.name, 'h264')
        self.assertEqual(container.streams[1].width, 640)
        self.assertEqual(container.streams[1].height, 480)
        self.assertGreater(
            float(container.streams[1].duration * container.streams[1].time_base), 0)

    def test_video_png(self):
        path = self.temporary_path('test-%3d.png')
        recorder = MediaRecorder(path)
        recorder.addTrack(VideoStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

        # check output media
        container = av.open(path, 'r')
        self.assertEqual(len(container.streams), 1)
        self.assertEqual(container.streams[0].codec.name, 'png')
        self.assertGreater(
            float(container.streams[0].duration * container.streams[0].time_base), 0)
        self.assertEqual(container.streams[0].width, 640)
        self.assertEqual(container.streams[0].height, 480)

    def test_video_mp4(self):
        path = self.temporary_path('test.mp4')
        recorder = MediaRecorder(path)
        recorder.addTrack(VideoStreamTrack())
        run(recorder.start())
        run(asyncio.sleep(2))
        run(recorder.stop())

        # check output media
        container = av.open(path, 'r')
        self.assertEqual(len(container.streams), 1)
        self.assertEqual(container.streams[0].codec.name, 'h264')
        self.assertGreater(
            float(container.streams[0].duration * container.streams[0].time_base), 0)
        self.assertEqual(container.streams[0].width, 640)
        self.assertEqual(container.streams[0].height, 480)
