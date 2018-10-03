import asyncio
import os
import wave
from unittest import TestCase

import cv2
import numpy
from av import VideoFrame

from aiortc import AudioStreamTrack, VideoStreamTrack
from aiortc.contrib.media import (MediaBlackhole, MediaPlayer, MediaRecorder,
                                  video_frame_from_bgr)

from .utils import run


def create_audio(path, channels=1, sample_rate=8000, sample_width=2):
    writer = wave.open(path, 'wb')
    writer.setnchannels(channels)
    writer.setframerate(sample_rate)
    writer.setsampwidth(sample_width)

    writer.writeframes(b'\x00' * sample_rate * sample_width * channels)
    writer.close()


def create_video(path, width=640, height=480, fps=20, duration=1):
    fourcc = cv2.VideoWriter_fourcc(*'XVID')
    out = cv2.VideoWriter(path, fourcc, fps, (width, height))

    frames = duration * fps
    for i in range(frames):
        s = i * 256 // frames
        pixel = (s, 256 - s, (128 - 2 * s) % 256)
        image = numpy.full((height, width, 3), pixel, numpy.uint8)
        out.write(image)
    out.release()


class MediaBlackholeTest(TestCase):
    def test_audio(self):
        recorder = MediaBlackhole()
        recorder.addTrack(AudioStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()

    def test_audio_remove_track(self):
        recorder = MediaBlackhole()
        track = AudioStreamTrack()
        recorder.addTrack(track)
        recorder.start()
        run(asyncio.sleep(1))
        recorder.removeTrack(track)
        run(asyncio.sleep(1))
        recorder.stop()

    def test_audio_and_video(self):
        recorder = MediaBlackhole()
        recorder.addTrack(AudioStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()

    def test_video(self):
        recorder = MediaBlackhole()
        recorder.addTrack(VideoStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()


class MediaPlayerTest(TestCase):
    def setUp(self):
        self.audio_path = os.path.join(os.path.dirname(__file__), 'test.wav')
        create_audio(self.audio_path)

        self.video_path = os.path.join(os.path.dirname(__file__), 'test.avi')
        create_video(self.video_path)

    def tearDown(self):
        os.unlink(self.audio_path)
        os.unlink(self.video_path)

    def test_audio_file_8kHz(self):
        player = MediaPlayer(path=self.audio_path)

        # check tracks
        self.assertIsNotNone(player.audio)
        self.assertIsNone(player.video)

        # read all frames
        player.start()
        for i in range(49):
            frame = run(player.audio.recv())
            self.assertEqual(frame.format.name, 's16')
            self.assertEqual(frame.layout.name, 'mono')
            self.assertEqual(frame.samples, 960)
            self.assertEqual(frame.sample_rate, 48000)
        player.stop()

    def test_audio_file_48kHz(self):
        create_audio(self.audio_path, sample_rate=48000)
        player = MediaPlayer(path=self.audio_path)

        # check tracks
        self.assertIsNotNone(player.audio)
        self.assertIsNone(player.video)

        # read all frames
        player.start()
        for i in range(50):
            frame = run(player.audio.recv())
            self.assertEqual(frame.format.name, 's16')
            self.assertEqual(frame.layout.name, 'mono')
            self.assertEqual(frame.samples, 960)
            self.assertEqual(frame.sample_rate, 48000)
        player.stop()

    def test_video_file(self):
        player = MediaPlayer(path=self.video_path)

        # check tracks
        self.assertIsNone(player.audio)
        self.assertIsNotNone(player.video)

        # read all frames
        player.start()
        for i in range(20):
            frame = run(player.video.recv())
            self.assertEqual(frame.width, 640)
            self.assertEqual(frame.height, 480)
        player.stop()


class MediaRecorderTest(TestCase):
    def test_audio_mp3(self):
        recorder = MediaRecorder(path='foo.mp3')
        recorder.addTrack(AudioStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()

    def test_audio_wav(self):
        recorder = MediaRecorder(path='foo.wav')
        recorder.addTrack(AudioStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()

    def test_audio_and_video(self):
        recorder = MediaRecorder(path='foo.mp4')
        recorder.addTrack(AudioStreamTrack())
        recorder.addTrack(VideoStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()

    def test_video_jpg(self):
        recorder = MediaRecorder(path='foo-%3d.jpg')
        recorder.addTrack(VideoStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()

    def test_video_mp4(self):
        recorder = MediaRecorder(path='foo.mp4')
        recorder.addTrack(VideoStreamTrack())
        recorder.start()
        run(asyncio.sleep(2))
        recorder.stop()


class VideoFrameTest(TestCase):
    def test_video_frame_from_bgr(self):
        image = numpy.full((480, 640, 3), (0, 0, 0), numpy.uint8)
        frame = video_frame_from_bgr(image, timestamp=123)
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)
        self.assertEqual(frame.pts, 123)
