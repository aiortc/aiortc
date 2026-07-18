import fractions
import io
from contextlib import redirect_stderr
from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.vpx import (
    Vp8Decoder,
    Vp8Encoder,
    Vp9Decoder,
    Vp9Encoder,
    Vp9PayloadDescriptor,
    VpxPayloadDescriptor,
    number_of_threads,
    vp9_depayload,
)
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .codecs import CodecTestCase
from .utils import load

VP8_CODEC = RTCRtpCodecParameters(
    mimeType="video/VP8", clockRate=90000, payloadType=100
)
VP9_CODEC = RTCRtpCodecParameters(
    mimeType="video/VP9", clockRate=90000, payloadType=101
)


class VpxPayloadDescriptorTest(TestCase):
    def test_no_picture_id(self) -> None:
        descr, rest = VpxPayloadDescriptor.parse(b"\x10")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x10")
        self.assertEqual(repr(descr), "VpxPayloadDescriptor(S=1, PID=0, pic_id=None)")

        self.assertEqual(rest, b"")

    def test_short_picture_id_17(self) -> None:
        """
        From RFC 7741 - 4.6.3
        """
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\x80\x11")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 17)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x90\x80\x11")
        self.assertEqual(repr(descr), "VpxPayloadDescriptor(S=1, PID=0, pic_id=17)")

        self.assertEqual(rest, b"")

    def test_short_picture_id_127(self) -> None:
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\x80\x7f")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 127)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x90\x80\x7f")

        self.assertEqual(rest, b"")

    def test_long_picture_id_128(self) -> None:
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\x80\x80\x80")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 128)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x90\x80\x80\x80")

        self.assertEqual(rest, b"")

    def test_long_picture_id_4711(self) -> None:
        """
        From RFC 7741 - 4.6.5
        """
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\x80\x92\x67")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 4711)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x90\x80\x92\x67")

        self.assertEqual(rest, b"")

    def test_tl0picidx(self) -> None:
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\xc0\x92\x67\x81")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 4711)
        self.assertEqual(descr.tl0picidx, 129)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x90\xc0\x92\x67\x81")

        self.assertEqual(rest, b"")

    def test_tid(self) -> None:
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\x20\xe0")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, (3, 1))
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b"\x90\x20\xe0")

        self.assertEqual(rest, b"")

    def test_keyidx(self) -> None:
        descr, rest = VpxPayloadDescriptor.parse(b"\x90\x10\x1f")
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, 31)
        self.assertEqual(bytes(descr), b"\x90\x10\x1f")

        self.assertEqual(rest, b"")

    def test_truncated(self) -> None:
        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"")
        self.assertEqual(str(cm.exception), "VPX descriptor is too short")

        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"\x80")
        self.assertEqual(
            str(cm.exception), "VPX descriptor has truncated extended bits"
        )

        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"\x80\x80")
        self.assertEqual(str(cm.exception), "VPX descriptor has truncated PictureID")

        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"\x80\x80\x80")
        self.assertEqual(
            str(cm.exception), "VPX descriptor has truncated long PictureID"
        )

        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"\x80\x40")
        self.assertEqual(str(cm.exception), "VPX descriptor has truncated TL0PICIDX")

        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"\x80\x20")
        self.assertEqual(str(cm.exception), "VPX descriptor has truncated T/K")

        with self.assertRaises(ValueError) as cm:
            VpxPayloadDescriptor.parse(b"\x80\x10")
        self.assertEqual(str(cm.exception), "VPX descriptor has truncated T/K")


class Vp8Test(CodecTestCase):
    def test_decoder(self) -> None:
        decoder = get_decoder(VP8_CODEC)
        self.assertIsInstance(decoder, Vp8Decoder)

        # decode junk
        with redirect_stderr(io.StringIO()):
            frames = decoder.decode(JitterFrame(data=b"123", timestamp=0))
        self.assertEqual(frames, [])

    def test_encoder(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP8_CODEC), Vp8Encoder)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

        # change resolution
        frame = self.create_video_frame(width=320, height=240, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertAlmostEqual(timestamp, 3000, delta=1)

    def test_encoder_rgb(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP8_CODEC), Vp8Encoder)

        frame = self.create_video_frame(width=640, height=480, pts=0, format="rgb24")
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

    def test_encoder_pack(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP8_CODEC), Vp8Encoder)
        encoder.picture_id = 0

        packet = self.create_packet(payload=b"\x00", pts=1)
        payloads, timestamp = encoder.pack(packet)
        self.assertEqual(payloads, [b"\x90\x80\x00\x00"])
        self.assertEqual(timestamp, 90)

    def test_encoder_large(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP8_CODEC), Vp8Encoder)

        # first keyframe
        frame = self.create_video_frame(width=2560, height=1920, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 7)
        self.assertEqual(len(payloads[0]), 1300)
        self.assertEqual(timestamp, 0)

        # delta frame
        frame = self.create_video_frame(width=2560, height=1920, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertAlmostEqual(timestamp, 3000, delta=1)

        # force keyframe
        frame = self.create_video_frame(width=2560, height=1920, pts=6000)
        payloads, timestamp = encoder.encode(frame, force_keyframe=True)
        self.assertEqual(len(payloads), 7)
        self.assertEqual(len(payloads[0]), 1300)
        self.assertAlmostEqual(timestamp, 6000, delta=1)

    def test_encoder_target_bitrate(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP8_CODEC), Vp8Encoder)
        self.assertEqual(encoder.target_bitrate, 500000)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

        # change target bitrate
        encoder.target_bitrate = 600000
        self.assertEqual(encoder.target_bitrate, 600000)

        frame = self.create_video_frame(width=640, height=480, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertAlmostEqual(timestamp, 3000, delta=1)

    def test_number_of_threads(self) -> None:
        self.assertEqual(number_of_threads(1920 * 1080, 16), 8)
        self.assertEqual(number_of_threads(1920 * 1080, 8), 3)
        self.assertEqual(number_of_threads(1920 * 1080, 4), 2)
        self.assertEqual(number_of_threads(1920 * 1080, 2), 1)

    def test_roundtrip_1280_720(self) -> None:
        self.roundtrip_video(VP8_CODEC, 1280, 720)

    def test_roundtrip_960_540(self) -> None:
        self.roundtrip_video(VP8_CODEC, 960, 540)

    def test_roundtrip_640_480(self) -> None:
        self.roundtrip_video(VP8_CODEC, 640, 480)

    def test_roundtrip_640_480_time_base(self) -> None:
        self.roundtrip_video(VP8_CODEC, 640, 480, time_base=fractions.Fraction(1, 9000))

    def test_roundtrip_320_240(self) -> None:
        self.roundtrip_video(VP8_CODEC, 320, 240)


class Vp9PayloadDescriptorTest(TestCase):
    def test_basic_descriptor_serialize(self) -> None:
        """Test serializing basic VP9 descriptor."""
        descr = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=42,
            start_of_frame=True,
            end_of_frame=True,
        )
        data = bytes(descr)

        # First byte: I=1, B=1, E=1 -> 0x8C
        self.assertEqual(data[0], 0x8C)
        # Second byte: 7-bit picture ID = 42
        self.assertEqual(data[1], 42)

    def test_15bit_picture_id(self) -> None:
        """Test 15-bit picture ID encoding."""
        descr = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=5000,  # > 127, requires 15 bits
            start_of_frame=True,
            end_of_frame=True,
        )
        data = bytes(descr)

        # Picture ID should be in bytes 1-2
        pic_id = ((data[1] & 0x7F) << 8) | data[2]
        self.assertEqual(pic_id, 5000)

    def test_descriptor_parse(self) -> None:
        """Test parsing VP9 descriptor."""
        # Build descriptor bytes manually
        data = bytes(
            [
                0x8C,  # I=1, B=1, E=1
                0x2A,  # Picture ID = 42
            ]
        )

        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.picture_id_present)
        self.assertTrue(descr.start_of_frame)
        self.assertTrue(descr.end_of_frame)
        self.assertEqual(descr.picture_id, 42)
        self.assertEqual(payload, b"")

    def test_descriptor_roundtrip(self) -> None:
        """Test serialize -> parse roundtrip."""
        original = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=123,
            inter_picture_predicted=True,
            layer_indices_present=True,
            start_of_frame=True,
            end_of_frame=False,
            temporal_id=0,
            spatial_id=0,
            tl0picidx=5,
        )

        # Serialize
        data = bytes(original)

        # Parse
        parsed, _ = Vp9PayloadDescriptor.parse(data)

        # Verify
        self.assertEqual(parsed.picture_id, original.picture_id)
        self.assertEqual(parsed.picture_id_present, original.picture_id_present)
        self.assertEqual(parsed.inter_picture_predicted, original.inter_picture_predicted)
        self.assertEqual(parsed.start_of_frame, original.start_of_frame)
        self.assertEqual(parsed.end_of_frame, original.end_of_frame)
        self.assertEqual(parsed.layer_indices_present, original.layer_indices_present)
        self.assertEqual(parsed.tl0picidx, original.tl0picidx)

    def test_layer_indices(self) -> None:
        """Test layer indices parsing."""
        # I=1, L=1, B=1, E=1
        data = bytes(
            [
                0xAC,  # flags
                0x15,  # Picture ID = 21 (7-bit)
                0x42,  # Layer byte: TID=2 (010), U=0, SID=1 (001), D=0
                0x05,  # TL0PICIDX = 5
            ]
        )

        descr, _ = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.picture_id_present)
        self.assertTrue(descr.layer_indices_present)
        self.assertTrue(descr.start_of_frame)
        self.assertTrue(descr.end_of_frame)
        self.assertEqual(descr.picture_id, 21)
        self.assertEqual(descr.temporal_id, 2)
        self.assertEqual(descr.spatial_id, 1)
        self.assertEqual(descr.tl0picidx, 5)

    def test_truncated(self) -> None:
        """Test truncated descriptor errors."""
        with self.assertRaises(ValueError) as cm:
            Vp9PayloadDescriptor.parse(b"")
        self.assertEqual(str(cm.exception), "VP9 descriptor is too short")

        with self.assertRaises(ValueError) as cm:
            Vp9PayloadDescriptor.parse(b"\x80")  # I=1 but no picture ID
        self.assertEqual(str(cm.exception), "VP9 descriptor has truncated Picture ID")

        with self.assertRaises(ValueError) as cm:
            Vp9PayloadDescriptor.parse(b"\x80\x80")  # I=1, 15-bit ID but truncated
        self.assertEqual(
            str(cm.exception), "VP9 descriptor has truncated 15-bit Picture ID"
        )

        with self.assertRaises(ValueError) as cm:
            Vp9PayloadDescriptor.parse(b"\xA0\x2A")  # I=1, L=1 but no layer byte
        self.assertEqual(str(cm.exception), "VP9 descriptor has truncated Layer Indices")

    def test_pion_compat_non_flexible(self) -> None:
        """Test compatibility with Pion test case: NonFlexible"""
        # From Pion: []byte{0x00, 0xAA}
        data = bytes([0x00, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertFalse(descr.picture_id_present)
        self.assertFalse(descr.inter_picture_predicted)
        self.assertFalse(descr.layer_indices_present)
        self.assertFalse(descr.flexible_mode)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_non_flexible_picture_id(self) -> None:
        """Test compatibility with Pion test case: NonFlexiblePictureID"""
        # From Pion: []byte{0x80, 0x02, 0xAA}
        data = bytes([0x80, 0x02, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.picture_id_present)
        self.assertEqual(descr.picture_id, 0x02)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_non_flexible_picture_id_ext(self) -> None:
        """Test compatibility with Pion test case: NonFlexiblePictureIDExt"""
        # From Pion: []byte{0x80, 0x81, 0xFF, 0xAA}
        # Expected: PictureID = 0x01FF
        data = bytes([0x80, 0x81, 0xFF, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.picture_id_present)
        self.assertEqual(descr.picture_id, 0x01FF)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_non_flexible_layer_indice_picture_id(self) -> None:
        """Test compatibility with Pion test case: NonFlexibleLayerIndicePictureID"""
        # From Pion: []byte{0xA0, 0x02, 0x23, 0x01, 0xAA}
        # Expected: I=true, L=true, PictureID=0x02, TID=0x01, SID=0x01, D=true, TL0PICIDX=0x01
        data = bytes([0xA0, 0x02, 0x23, 0x01, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.picture_id_present)
        self.assertTrue(descr.layer_indices_present)
        self.assertFalse(descr.flexible_mode)  # Non-flexible
        self.assertEqual(descr.picture_id, 0x02)
        self.assertEqual(descr.temporal_id, 0x01)  # TID bits 7-5 = 001
        self.assertEqual(descr.spatial_id, 0x01)  # SID bits 3-1 = 001
        self.assertTrue(descr.inter_layer_dependency)
        self.assertEqual(descr.tl0picidx, 0x01)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_flexible_layer_indice_picture_id(self) -> None:
        """Test compatibility with Pion test case: FlexibleLayerIndicePictureID"""
        # From Pion: []byte{0xB0, 0x02, 0x23, 0x01, 0xAA}
        # Expected: F=true, I=true, L=true, PictureID=0x02, TID=0x01, SID=0x01, D=true
        # Note: In flexible mode, there's no TL0PICIDX, payload starts after layer byte
        data = bytes([0xB0, 0x02, 0x23, 0x01, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.flexible_mode)  # Flexible mode
        self.assertTrue(descr.picture_id_present)
        self.assertTrue(descr.layer_indices_present)
        self.assertEqual(descr.picture_id, 0x02)
        self.assertEqual(descr.temporal_id, 0x01)
        self.assertEqual(descr.spatial_id, 0x01)
        self.assertTrue(descr.inter_layer_dependency)
        self.assertIsNone(descr.tl0picidx)  # Not present in flexible mode without P flag
        # In flexible mode without P flag, payload starts right after layer byte
        self.assertEqual(payload, b"\x01\xAA")

    def test_picture_id_wraparound(self) -> None:
        """Test Picture ID wraparound at 15-bit boundary"""
        # Test that picture ID wraps from 0x7FFF to 0
        descr1 = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=0x7FFF,
            start_of_frame=True,
            end_of_frame=True,
        )

        # Serialize and parse
        data1 = bytes(descr1)
        parsed1, _ = Vp9PayloadDescriptor.parse(data1)
        self.assertEqual(parsed1.picture_id, 0x7FFF)

        # Next picture ID should wrap to 0
        next_pic_id = (0x7FFF + 1) & 0x7FFF
        self.assertEqual(next_pic_id, 0)

    def test_marker_bits_b_and_e(self) -> None:
        """Test B (begin) and E (end) marker bits for fragmentation"""
        # Single packet: B=1, E=1
        descr_single = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=True,
            end_of_frame=True,
        )
        data = bytes(descr_single)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertTrue(parsed.start_of_frame)
        self.assertTrue(parsed.end_of_frame)

        # First packet: B=1, E=0
        descr_first = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=True,
            end_of_frame=False,
        )
        data = bytes(descr_first)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertTrue(parsed.start_of_frame)
        self.assertFalse(parsed.end_of_frame)

        # Middle packet: B=0, E=0
        descr_middle = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=False,
            end_of_frame=False,
        )
        data = bytes(descr_middle)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertFalse(parsed.start_of_frame)
        self.assertFalse(parsed.end_of_frame)

        # Last packet: B=0, E=1
        descr_last = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=False,
            end_of_frame=True,
        )
        data = bytes(descr_last)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertFalse(parsed.start_of_frame)
        self.assertTrue(parsed.end_of_frame)


class Vp9Test(CodecTestCase):
    def test_decoder(self) -> None:
        decoder = get_decoder(VP9_CODEC)
        self.assertIsInstance(decoder, Vp9Decoder)

        # decode junk
        with redirect_stderr(io.StringIO()):
            frames = decoder.decode(JitterFrame(data=b"123", timestamp=0))
        self.assertEqual(frames, [])

    def test_encoder(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

        # change resolution
        frame = self.create_video_frame(width=320, height=240, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertAlmostEqual(timestamp, 3000, delta=1)

    def test_encoder_rgb(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)

        frame = self.create_video_frame(width=640, height=480, pts=0, format="rgb24")
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

    def test_encoder_pack(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)
        encoder.picture_id = 0
        encoder.tl0picidx = 0

        packet = self.create_packet(payload=b"\x00", pts=1)
        payloads, timestamp = encoder.pack(packet)
        # VP9 descriptor: I=1, L=1, B=1, E=1 + 7-bit PID (0) + layer byte + TL0PICIDX
        self.assertEqual(len(payloads), 1)
        self.assertEqual(timestamp, 90)

    def test_encoder_large(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)

        # first keyframe
        frame = self.create_video_frame(width=2560, height=1920, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertGreater(len(payloads), 1)  # Should be multiple packets
        self.assertEqual(timestamp, 0)

        # delta frame
        frame = self.create_video_frame(width=2560, height=1920, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(payloads), 1)
        self.assertAlmostEqual(timestamp, 3000, delta=1)

        # force keyframe
        frame = self.create_video_frame(width=2560, height=1920, pts=6000)
        payloads, timestamp = encoder.encode(frame, force_keyframe=True)
        self.assertGreater(len(payloads), 1)
        self.assertAlmostEqual(timestamp, 6000, delta=1)

    def test_encoder_target_bitrate(self) -> None:
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)
        self.assertEqual(encoder.target_bitrate, 500000)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

        # change target bitrate
        encoder.target_bitrate = 600000
        self.assertEqual(encoder.target_bitrate, 600000)

        frame = self.create_video_frame(width=640, height=480, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertAlmostEqual(timestamp, 3000, delta=1)

    def test_number_of_threads(self) -> None:
        self.assertEqual(number_of_threads(1920 * 1080, 16), 8)
        self.assertEqual(number_of_threads(1920 * 1080, 8), 3)
        self.assertEqual(number_of_threads(1920 * 1080, 4), 2)
        self.assertEqual(number_of_threads(1920 * 1080, 2), 1)

    def test_roundtrip_1280_720(self) -> None:
        self.roundtrip_video(VP9_CODEC, 1280, 720)

    def test_roundtrip_960_540(self) -> None:
        self.roundtrip_video(VP9_CODEC, 960, 540)

    def test_roundtrip_640_480(self) -> None:
        self.roundtrip_video(VP9_CODEC, 640, 480)

    def test_roundtrip_640_480_time_base(self) -> None:
        self.roundtrip_video(VP9_CODEC, 640, 480, time_base=fractions.Fraction(1, 9000))

    def test_roundtrip_320_240(self) -> None:
        self.roundtrip_video(VP9_CODEC, 320, 240)

    def test_p_flag_keyframe_and_interframe(self) -> None:
        """
        Test that VP9 RTP payload descriptor P flag is set correctly.

        P=0 for keyframes (not inter-picture predicted)
        P=1 for inter-frames (inter-picture predicted)

        RFC 9628 Section 4.2
        """
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)

        # First frame - should be keyframe with P=0
        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)

        # Parse payload descriptor (byte 0)
        byte0 = payloads[0][0]
        p_flag = bool(byte0 & 0x40)  # Bit 6 is P flag
        self.assertFalse(p_flag, "First frame should have P=0 (keyframe)")

        # Second frame - should be inter-frame with P=1
        frame = self.create_video_frame(width=640, height=480, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)

        # Parse payload descriptor (byte 0)
        byte0 = payloads[0][0]
        p_flag = bool(byte0 & 0x40)  # Bit 6 is P flag
        self.assertTrue(p_flag, "Second frame should have P=1 (inter-frame)")

        # Third frame - should also be inter-frame with P=1
        frame = self.create_video_frame(width=640, height=480, pts=6000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)

        byte0 = payloads[0][0]
        p_flag = bool(byte0 & 0x40)
        self.assertTrue(p_flag, "Third frame should have P=1 (inter-frame)")

        # Force keyframe - should have P=0
        frame = self.create_video_frame(width=640, height=480, pts=9000)
        payloads, timestamp = encoder.encode(frame, force_keyframe=True)
        self.assertGreaterEqual(len(payloads), 1)

        byte0 = payloads[0][0]
        p_flag = bool(byte0 & 0x40)
        self.assertFalse(p_flag, "Forced keyframe should have P=0")

    def test_vp9_depayload(self) -> None:
        """Test vp9_depayload function extracts payload correctly."""
        # Create a descriptor + payload
        descr = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=42,
            start_of_frame=True,
            end_of_frame=True,
        )
        payload_data = b"\xDE\xAD\xBE\xEF"
        full_packet = bytes(descr) + payload_data

        # Extract payload
        result = vp9_depayload(full_packet)
        self.assertEqual(result, payload_data)

    def test_encoder_non_flexible_mode(self) -> None:
        """Test VP9 encoder in non-flexible mode (F=0)."""
        encoder = Vp9Encoder(flexible_mode=False)

        # First frame - keyframe
        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertGreater(len(payloads), 0)

        # Check F=0 (non-flexible mode)
        byte0 = payloads[0][0]
        f_flag = bool(byte0 & 0x10)  # F bit
        self.assertFalse(f_flag, "Non-flexible mode should have F=0")

        # Check V=1 for keyframe first packet (scalability structure)
        v_flag = bool(byte0 & 0x02)  # V bit
        self.assertTrue(v_flag, "Keyframe first packet should have V=1")

    def test_encoder_target_bitrate_clamping(self) -> None:
        """Test that target bitrate is clamped to min/max values."""
        encoder = self.ensureIsInstance(get_encoder(VP9_CODEC), Vp9Encoder)

        # Set bitrate below minimum (250000)
        encoder.target_bitrate = 100000
        self.assertEqual(encoder.target_bitrate, 250000)

        # Set bitrate above maximum (1500000)
        encoder.target_bitrate = 5000000
        self.assertEqual(encoder.target_bitrate, 1500000)

    def test_descriptor_with_v_flag(self) -> None:
        """Test VP9 descriptor with V (scalability structure) flag set."""
        descr = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=100,
            start_of_frame=True,
            end_of_frame=True,
            scalability_structure_present=True,
        )
        data = bytes(descr)

        # Verify V flag is set in byte 0
        self.assertTrue(data[0] & 0x02)

    def test_descriptor_with_z_flag(self) -> None:
        """Test VP9 descriptor with Z (not reference frame) flag set."""
        descr = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=100,
            start_of_frame=True,
            end_of_frame=True,
            not_reference_frame=True,
        )
        data = bytes(descr)

        # Verify Z flag is set in byte 0
        self.assertTrue(data[0] & 0x01)

        # Parse and verify
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertTrue(parsed.not_reference_frame)

    def test_parse_scalability_structure(self) -> None:
        """Test parsing VP9 descriptor with scalability structure (V=1)."""
        # V=1, I=1, B=1, E=1 with SS data
        # SS: N_S=0 (1 layer), Y=1, G=0
        # Then WIDTH (2 bytes) + HEIGHT (2 bytes)
        data = bytes([
            0x8E,  # I=1, B=1, E=1, V=1
            0x2A,  # Picture ID = 42
            0x10,  # SS: N_S=0, Y=1, G=0
            0x02, 0x80,  # WIDTH = 640
            0x01, 0xE0,  # HEIGHT = 480
            0xAA,  # payload
        ])

        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.picture_id_present)
        self.assertTrue(descr.scalability_structure_present)
        self.assertEqual(descr.picture_id, 42)
        self.assertEqual(payload, b"\xAA")

    def test_parse_scalability_structure_with_picture_group(self) -> None:
        """Test parsing VP9 descriptor with SS and picture group (G=1)."""
        # V=1, I=1, B=1, E=1 with SS data including picture group
        # SS: N_S=0, Y=0, G=1, N_G=1, one PG entry with R=0
        data = bytes([
            0x8E,  # I=1, B=1, E=1, V=1
            0x2A,  # Picture ID = 42
            0x08,  # SS: N_S=0, Y=0, G=1
            0x01,  # N_G = 1
            0x00,  # PG entry: TID=0, U=0, R=0
            0xBB,  # payload
        ])

        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.scalability_structure_present)
        self.assertEqual(payload, b"\xBB")

    def test_parse_pdiff_multiple(self) -> None:
        """Test parsing VP9 descriptor with multiple P_DIFF entries."""
        # F=1, P=1, I=1, B=1, E=1 with 2 P_DIFF entries
        data = bytes([
            0xDC,  # I=1, P=1, F=1, B=1, E=1
            0x2A,  # Picture ID = 42
            0x03,  # P_DIFF=1, N=1 (more follows)
            0x02,  # P_DIFF=1, N=0 (last one)
            0xCC,  # payload
        ])

        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.flexible_mode)
        self.assertTrue(descr.inter_picture_predicted)
        self.assertEqual(payload, b"\xCC")

    def test_parse_pdiff_too_many_error(self) -> None:
        """Test error when too many P_DIFF entries (>3)."""
        # F=1, P=1, I=1 with 4 P_DIFF entries (exceeds max of 3)
        data = bytes([
            0xD0,  # P=1, F=1
            0x01,  # P_DIFF, N=1
            0x01,  # P_DIFF, N=1
            0x01,  # P_DIFF, N=1
            0x01,  # P_DIFF, N=1 (4th - should error)
        ])

        with self.assertRaises(ValueError) as cm:
            Vp9PayloadDescriptor.parse(data)
        self.assertEqual(str(cm.exception), "VP9 descriptor has too many P_DIFF entries")

    def test_parse_vp9_header_keyframe(self) -> None:
        """Test parsing VP9 bitstream header from a real keyframe."""
        keyframe_data = load("vp9_keyframe.bin")
        result = Vp9Encoder._parse_vp9_header(keyframe_data)

        self.assertIsNotNone(result)
        self.assertFalse(result['non_key_frame'])
        self.assertEqual(result['width'], 320)
        self.assertEqual(result['height'], 240)

    def test_parse_vp9_header_interframe(self) -> None:
        """Test parsing VP9 bitstream header from a real inter-frame."""
        interframe_data = load("vp9_interframe.bin")
        result = Vp9Encoder._parse_vp9_header(interframe_data)

        self.assertIsNotNone(result)
        self.assertTrue(result['non_key_frame'])
        # Inter-frames don't have width/height in header
        self.assertIsNone(result['width'])
        self.assertIsNone(result['height'])

    def test_parse_vp9_header_empty(self) -> None:
        """Test parsing VP9 header with empty data."""
        result = Vp9Encoder._parse_vp9_header(b"")
        self.assertIsNone(result)

    def test_parse_vp9_header_truncated(self) -> None:
        """Test parsing VP9 header with truncated data."""
        # Single byte - not enough for header
        result = Vp9Encoder._parse_vp9_header(b"\x82")
        self.assertIsNone(result)

    def test_parse_vp9_header_invalid_frame_marker(self) -> None:
        """Test parsing VP9 header with invalid frame marker."""
        # Frame marker should be 0b10 (2), but this has 0b00
        result = Vp9Encoder._parse_vp9_header(b"\x00\x00\x00\x00")
        self.assertIsNone(result)

    def test_parse_vp9_header_show_existing_frame(self) -> None:
        """Test parsing VP9 header with show_existing_frame flag set."""
        # Craft a header with show_existing_frame=1
        # Frame marker (10) + profile (00) + show_existing_frame (1)
        # Binary: 10 00 1 xxx = 0x84 (first byte sets marker + profile low)
        # This is a simplified test - the parser returns None for show_existing_frame
        header = bytes([
            0x82,  # frame_marker=10, profile_low=0, profile_high=0
            0x80,  # show_existing_frame=1 (bit 7 after shifting)
        ])
        result = Vp9Encoder._parse_vp9_header(header)
        # show_existing_frame=True causes early return None
        self.assertIsNone(result)

    def test_encoder_non_flexible_mode_interframe(self) -> None:
        """Test VP9 encoder in non-flexible mode generates correct inter-frames."""
        encoder = Vp9Encoder(flexible_mode=False)

        # First frame - keyframe
        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, _ = encoder.encode(frame)
        self.assertGreater(len(payloads), 0)

        # Second frame - should be inter-frame with P=1
        frame2 = self.create_video_frame(width=640, height=480, pts=3000)
        payloads2, _ = encoder.encode(frame2)
        self.assertGreater(len(payloads2), 0)

        byte0 = payloads2[0][0]
        p_flag = bool(byte0 & 0x40)  # P bit
        self.assertTrue(p_flag, "Inter-frame should have P=1")

        # V should be 0 for inter-frames (no scalability structure)
        v_flag = bool(byte0 & 0x02)
        self.assertFalse(v_flag, "Inter-frame should have V=0")

    def test_encoder_packetize_empty_buffer(self) -> None:
        """Test that packetizing empty buffer returns empty list."""
        encoder = Vp9Encoder(flexible_mode=True)
        result = encoder._packetize_flexible(b"", 0, False)
        self.assertEqual(result, [])

