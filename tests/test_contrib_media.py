import os
from unittest import TestCase

from aiortc.contrib.media import AudioFileTrack

from .utils import run


class FileTrackTest(TestCase):
    def test_audio_file_track(self):
        path = os.path.join(
            os.path.dirname(__file__), os.path.pardir, 'examples', 'server', 'demo-instruct.wav')
        track = AudioFileTrack(path=path)

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
