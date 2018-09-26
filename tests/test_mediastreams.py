from unittest import TestCase

from aiortc import AudioStreamTrack, VideoStreamTrack


class MediaStreamTrackTest(TestCase):
    def test_audio(self):
        track = AudioStreamTrack()
        self.assertEqual(track.kind, 'audio')
        self.assertEqual(len(track.id), 36)

    def test_video(self):
        track = VideoStreamTrack()
        self.assertEqual(track.kind, 'video')
        self.assertEqual(len(track.id), 36)
