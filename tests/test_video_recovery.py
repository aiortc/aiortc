"""
Tests that detect video recovery bugs in aiortc.
These tests use only the OLD public API, so they work on both
old and new code — failing on old, passing on new.
"""
import fractions
from unittest import TestCase

import av
from av.video.frame import VideoFrame

from aiortc.codecs.h264 import H264Decoder, H264Encoder
from aiortc.codecs.vpx import Vp8Decoder, Vp8Encoder
from aiortc.jitterbuffer import JitterFrame


class Vp8GopSizeTest(TestCase):
    def test_gop_size_is_reasonable(self) -> None:
        """
        VP8 encoder gop_size should be <= 60 (~2s at 30fps).
        Old code has gop_size=3000 (~100s), making recovery from
        packet loss nearly impossible without PLI.
        """
        encoder = Vp8Encoder()

        # Trigger codec creation by encoding a frame
        frame = VideoFrame(width=320, height=240, format="yuv420p")
        frame.pts = 0
        frame.time_base = fractions.Fraction(1, 90000)
        encoder.encode(frame)

        self.assertIsNotNone(encoder.codec)
        self.assertLessEqual(
            encoder.codec.gop_size,
            60,
            f"VP8 gop_size={encoder.codec.gop_size} is too large. "
            f"Should be <= 60 for timely keyframe insertion.",
        )


class H264GopSizeTest(TestCase):
    def test_gop_size_is_reasonable(self) -> None:
        """
        H264 encoder gop_size should be explicitly set to <= 60.
        Default libx264 gop_size=250 (~8s) is too large.
        """
        encoder = H264Encoder()

        frame = VideoFrame(width=320, height=240, format="yuv420p")
        frame.pts = 0
        frame.time_base = fractions.Fraction(1, 90000)
        # H264Encoder.encode is a generator
        list(encoder.encode(frame))

        self.assertIsNotNone(encoder.codec)
        self.assertLessEqual(
            encoder.codec.gop_size,
            60,
            f"H264 gop_size={encoder.codec.gop_size} is too large. "
            f"Should be <= 60 for timely keyframe insertion.",
        )


class Vp8DecoderResetTest(TestCase):
    def test_codec_is_recreated_after_error(self) -> None:
        """
        After a decode error, the VP8 CodecContext should be recreated
        to clear corrupted reference frames. Old code keeps the same
        broken codec, causing permanent garbage output.
        """
        decoder = Vp8Decoder()
        original_codec_id = id(decoder.codec)

        # Feed garbage to trigger decode error
        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        decoder.decode(bad_frame)

        self.assertNotEqual(
            id(decoder.codec),
            original_codec_id,
            "CodecContext was NOT recreated after decode error. "
            "Corrupted reference frames will cause permanent garbage output.",
        )

    def test_multiple_errors_always_recreate(self) -> None:
        """Each consecutive error should create a fresh codec."""
        decoder = Vp8Decoder()
        bad_frame = JitterFrame(data=b"\xff\xfe\xfd", timestamp=0)

        for _ in range(3):
            codec_before = decoder.codec
            decoder.decode(bad_frame)
            self.assertIsNot(
                decoder.codec,
                codec_before,
                "CodecContext should be different after each decode error.",
            )


class H264DecoderResetTest(TestCase):
    def test_codec_is_recreated_after_error(self) -> None:
        """
        After a decode error, the H264 CodecContext should be recreated.
        """
        decoder = H264Decoder()
        original_codec_id = id(decoder.codec)

        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        decoder.decode(bad_frame)

        self.assertNotEqual(
            id(decoder.codec),
            original_codec_id,
            "CodecContext was NOT recreated after decode error. "
            "Corrupted reference frames will cause permanent garbage output.",
        )


class DecoderErrorSignalingTest(TestCase):
    def test_vp8_decoder_tracks_errors(self) -> None:
        """
        VP8 decoder should expose decode_errors so decoder_worker can
        detect failures and request PLI. Old code has no such mechanism.
        """
        decoder = Vp8Decoder()

        # Before any error
        self.assertTrue(
            hasattr(decoder, "decode_errors"),
            "Vp8Decoder has no 'decode_errors' attribute. "
            "Decode failures are silently swallowed — no PLI is ever sent.",
        )
        self.assertEqual(decoder.decode_errors, 0)

        # After error
        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        decoder.decode(bad_frame)
        self.assertGreater(
            decoder.decode_errors,
            0,
            "decode_errors was not incremented after decode failure.",
        )

    def test_h264_decoder_tracks_errors(self) -> None:
        """H264 decoder should expose decode_errors."""
        decoder = H264Decoder()

        self.assertTrue(
            hasattr(decoder, "decode_errors"),
            "H264Decoder has no 'decode_errors' attribute. "
            "Decode failures are silently swallowed — no PLI is ever sent.",
        )

        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        decoder.decode(bad_frame)
        self.assertGreater(decoder.decode_errors, 0)
