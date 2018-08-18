import os
import wave
from unittest import TestCase

import cv2
import numpy

from aiortc import VideoFrame
from aiortc.contrib.media import (AudioFileTrack, VideoFileTrack,
                                  frame_from_bgr, frame_from_gray,
                                  frame_to_bgr)

from .utils import run


def create_audio(path, channels=1, sample_rate=8000, sample_width=2):
    writer = wave.open(path, 'wb')
    writer.setnchannels(channels)
    writer.setframerate(sample_rate)
    writer.setsampwidth(sample_width)

    writer.writeframes(b'\x00\x00' * sample_rate)
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


class FileTrackTest(TestCase):
    def setUp(self):
        self.audio_path = os.path.join(os.path.dirname(__file__), 'test.wav')
        create_audio(self.audio_path)

        self.video_path = os.path.join(os.path.dirname(__file__), 'test.avi')
        create_video(self.video_path)

    def tearDown(self):
        os.unlink(self.audio_path)
        os.unlink(self.video_path)

    def test_audio_file_track(self):
        track = AudioFileTrack(path=self.audio_path)

        # read enough frames to loop once
        for i in range(51):
            frame = run(track.recv())
            self.assertEqual(frame.channels, 1)
            self.assertEqual(len(frame.data), 320)
            self.assertEqual(frame.sample_rate, 8000)
            self.assertEqual(frame.sample_width, 2)

    def test_video_file_track(self):
        track = VideoFileTrack(path=self.video_path)

        # read enough frames to loop once
        for i in range(21):
            frame = run(track.recv())
            self.assertEqual(len(frame.data), 460800)
            self.assertEqual(frame.width, 640)
            self.assertEqual(frame.height, 480)


class VideoFrameTest(TestCase):
    def test_frame_from_bgr(self):
        image = numpy.full((480, 640, 3), (0, 0, 0), numpy.uint8)
        frame = frame_from_bgr(image)
        self.assertEqual(len(frame.data), 460800)
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)

    def test_frame_from_gray(self):
        image = numpy.full((480, 640), 0, numpy.uint8)
        frame = frame_from_gray(image)
        self.assertEqual(len(frame.data), 460800)
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)

    def test_frame_to_bgr(self):
        frame = VideoFrame(width=640, height=480)
        image = frame_to_bgr(frame)
        self.assertEqual(image.shape, (480, 640, 3))
