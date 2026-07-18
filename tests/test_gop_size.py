import fractions
from unittest import TestCase

from av.video.frame import VideoFrame

from aiortc.codecs.h264 import H264Encoder
from aiortc.codecs.vpx import Vp8Encoder


class Vp8GopSizeTest(TestCase):
    def test_gop_size_is_reasonable(self) -> None:
        """VP8 gop_size should be <= 60 for timely keyframe insertion."""
        encoder = Vp8Encoder()

        frame = VideoFrame(width=320, height=240, format="yuv420p")
        frame.pts = 0
        frame.time_base = fractions.Fraction(1, 90000)
        encoder.encode(frame)

        self.assertIsNotNone(encoder.codec)
        self.assertLessEqual(encoder.codec.gop_size, 60)


class H264GopSizeTest(TestCase):
    def test_gop_size_is_reasonable(self) -> None:
        """H264 gop_size should be explicitly set to <= 60."""
        encoder = H264Encoder()

        frame = VideoFrame(width=320, height=240, format="yuv420p")
        frame.pts = 0
        frame.time_base = fractions.Fraction(1, 90000)
        # H264Encoder._encode_frame is a generator
        list(encoder._encode_frame(frame, force_keyframe=False))

        self.assertIsNotNone(encoder.codec)
        self.assertLessEqual(encoder.codec.gop_size, 60)
