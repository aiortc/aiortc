"""
Tests that detect video decoder recovery bugs in aiortc.
These tests use only the OLD public API, so they work on both
old and new code — failing on old, passing on new.
"""
from unittest import TestCase

from aiortc.codecs.h264 import H264Decoder
from aiortc.codecs.vpx import Vp8Decoder
from aiortc.jitterbuffer import JitterFrame


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

    def test_subsequent_errors_do_not_reset(self) -> None:
        """Only first error resets codec. Subsequent errors drop silently
        to avoid PLI storm."""
        decoder = Vp8Decoder()
        bad_frame = JitterFrame(data=b"\xff\xfe\xfd", timestamp=0)

        # First error resets
        decoder.decode(bad_frame)
        codec_after_first = decoder.codec

        # Second error does NOT reset
        decoder.decode(bad_frame)
        self.assertIs(
            decoder.codec,
            codec_after_first,
            "CodecContext should NOT be recreated on subsequent errors.",
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
