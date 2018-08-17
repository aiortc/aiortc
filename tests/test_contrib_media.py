import os
from unittest import TestCase

import cv2
import numpy
from aiortc.contrib.media import AudioFileTrack, VideoFileTrack

from .utils import run


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
        self.audio_path = os.path.join(
            os.path.dirname(__file__), os.path.pardir, 'examples', 'server', 'demo-instruct.wav')

        self.video_path = os.path.join(os.path.dirname(__file__), 'test.avi')
        create_video(self.video_path)

    def tearDown(self):
        os.unlink(self.video_path)

    def test_audio_file_track(self):
        track = AudioFileTrack(path=self.audio_path)

        # read first frame
        frame = run(track.recv())
        self.assertEqual(frame.channels, 1)
        self.assertEqual(len(frame.data), 320)
        self.assertEqual(frame.sample_rate, 8000)
        self.assertEqual(frame.sample_width, 2)

        # read another frame
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
            self.assertEqual(frame.width, 640)
            self.assertEqual(frame.height, 480)
