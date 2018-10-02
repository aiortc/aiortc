import fractions
from unittest import TestCase

from aiortc import AudioFrame, AudioStreamTrack, VideoStreamTrack


class MediaFrameTest(TestCase):
    def test_audio(self):
        frame = AudioFrame(channels=1, data=bytes(320), sample_rate=8000)
        frame.pts = 160
        frame.time_base = fractions.Fraction(1, 8000)
        self.assertEqual(frame.time, 0.02)

    def test_audio_no_pts(self):
        frame = AudioFrame(channels=1, data=bytes(320), sample_rate=8000)
        frame.time_base = fractions.Fraction(1, 8000)
        self.assertEqual(frame.time, None)

    def test_audio_no_time_base(self):
        frame = AudioFrame(channels=1, data=bytes(320), sample_rate=8000)
        frame.pts = 160
        self.assertEqual(frame.time, None)


class MediaStreamTrackTest(TestCase):
    def test_audio(self):
        track = AudioStreamTrack()
        self.assertEqual(track.kind, 'audio')
        self.assertEqual(len(track.id), 36)

    def test_video(self):
        track = VideoStreamTrack()
        self.assertEqual(track.kind, 'video')
        self.assertEqual(len(track.id), 36)
