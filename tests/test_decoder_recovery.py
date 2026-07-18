import asyncio
import queue
import threading
from unittest import TestCase

from aiortc.codecs.h264 import H264Decoder
from aiortc.codecs.vpx import Vp8Decoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpreceiver import decoder_worker


class Vp8DecoderRecoveryTest(TestCase):
    def test_decode_error_resets_codec(self) -> None:
        """
        When VP8 decode fails, the CodecContext should be recreated
        and decode_errors should be incremented.
        """
        decoder = Vp8Decoder()
        original_codec = decoder.codec

        # Feed garbage data to trigger decode error
        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        result = decoder.decode(bad_frame)

        self.assertEqual(result, [])
        self.assertEqual(decoder.decode_errors, 1)
        # CodecContext should have been recreated
        self.assertIsNot(decoder.codec, original_codec)

    def test_decode_error_counter_defaults_to_zero(self) -> None:
        """A new decoder should initialize decode_errors to 0."""
        self.assertEqual(Vp8Decoder().decode_errors, 0)

    def test_only_first_error_resets_codec(self) -> None:
        """Only the first decode error should reset CodecContext.
        Subsequent errors silently drop to avoid PLI storm."""
        decoder = Vp8Decoder()
        original_codec = decoder.codec
        bad_frame = JitterFrame(data=b"\xff\xfe\xfd", timestamp=0)

        # First error: codec is reset
        decoder.decode(bad_frame)
        self.assertEqual(decoder.decode_errors, 1)
        first_reset_codec = decoder.codec
        self.assertIsNot(first_reset_codec, original_codec)

        # Second error: codec is NOT reset (same instance)
        decoder.decode(bad_frame)
        self.assertEqual(decoder.decode_errors, 2)
        self.assertIs(decoder.codec, first_reset_codec)

        # Third error: still not reset
        decoder.decode(bad_frame)
        self.assertEqual(decoder.decode_errors, 3)
        self.assertIs(decoder.codec, first_reset_codec)


class H264DecoderRecoveryTest(TestCase):
    def test_decode_error_resets_codec(self) -> None:
        """
        When H264 decode fails, the CodecContext should be recreated
        and decode_errors should be incremented.
        """
        decoder = H264Decoder()
        original_codec = decoder.codec

        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        result = decoder.decode(bad_frame)

        self.assertEqual(result, [])
        self.assertEqual(decoder.decode_errors, 1)
        self.assertIsNot(decoder.codec, original_codec)


class DecoderWorkerPliTest(TestCase):
    def test_pli_event_set_on_decode_error(self) -> None:
        """
        When the decoder fails to decode a frame, the PLI event should
        be set to signal the receiver to send a PLI.
        """
        loop = asyncio.new_event_loop()
        input_q: queue.Queue = queue.Queue()
        output_q: asyncio.Queue = asyncio.Queue()
        pli_event = threading.Event()

        # Create a VP8 codec parameter
        from aiortc.rtcrtpparameters import RTCRtpCodecParameters

        vp8_codec = RTCRtpCodecParameters(
            mimeType="video/VP8", clockRate=90000, payloadType=96
        )

        # Queue a bad frame then stop signal
        bad_frame = JitterFrame(data=b"\x00\x01\x02\x03", timestamp=0)
        input_q.put((vp8_codec, bad_frame))
        input_q.put(None)  # stop signal

        # Run decoder_worker in a thread
        t = threading.Thread(
            target=decoder_worker,
            args=(loop, input_q, output_q, pli_event),
        )
        t.start()
        t.join(timeout=5)

        # PLI event should have been set
        self.assertTrue(pli_event.is_set())

        loop.close()

    def test_pli_event_not_set_on_no_error(self) -> None:
        """
        When there's no decode error, PLI event should NOT be set.
        This test just verifies the event starts unset and a stop
        signal alone doesn't trigger it.
        """
        loop = asyncio.new_event_loop()
        input_q: queue.Queue = queue.Queue()
        output_q: asyncio.Queue = asyncio.Queue()
        pli_event = threading.Event()

        input_q.put(None)  # immediate stop

        t = threading.Thread(
            target=decoder_worker,
            args=(loop, input_q, output_q, pli_event),
        )
        t.start()
        t.join(timeout=5)

        self.assertFalse(pli_event.is_set())

        loop.close()
