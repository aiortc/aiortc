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
)
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .codecs import CodecTestCase

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

        self.assertTrue(descr.I)
        self.assertTrue(descr.B)
        self.assertTrue(descr.E)
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
        self.assertEqual(parsed.I, original.I)
        self.assertEqual(parsed.P, original.P)
        self.assertEqual(parsed.B, original.B)
        self.assertEqual(parsed.E, original.E)
        self.assertEqual(parsed.L, original.L)
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

        self.assertTrue(descr.I)
        self.assertTrue(descr.L)
        self.assertTrue(descr.B)
        self.assertTrue(descr.E)
        self.assertEqual(descr.picture_id, 21)
        self.assertEqual(descr.tid, 2)
        self.assertEqual(descr.sid, 1)
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

        self.assertFalse(descr.I)
        self.assertFalse(descr.P)
        self.assertFalse(descr.L)
        self.assertFalse(descr.F)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_non_flexible_picture_id(self) -> None:
        """Test compatibility with Pion test case: NonFlexiblePictureID"""
        # From Pion: []byte{0x80, 0x02, 0xAA}
        data = bytes([0x80, 0x02, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.I)
        self.assertEqual(descr.picture_id, 0x02)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_non_flexible_picture_id_ext(self) -> None:
        """Test compatibility with Pion test case: NonFlexiblePictureIDExt"""
        # From Pion: []byte{0x80, 0x81, 0xFF, 0xAA}
        # Expected: PictureID = 0x01FF
        data = bytes([0x80, 0x81, 0xFF, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.I)
        self.assertEqual(descr.picture_id, 0x01FF)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_non_flexible_layer_indice_picture_id(self) -> None:
        """Test compatibility with Pion test case: NonFlexibleLayerIndicePictureID"""
        # From Pion: []byte{0xA0, 0x02, 0x23, 0x01, 0xAA}
        # Expected: I=true, L=true, PictureID=0x02, TID=0x01, SID=0x01, D=true, TL0PICIDX=0x01
        data = bytes([0xA0, 0x02, 0x23, 0x01, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.I)
        self.assertTrue(descr.L)
        self.assertFalse(descr.F)  # Non-flexible
        self.assertEqual(descr.picture_id, 0x02)
        self.assertEqual(descr.tid, 0x01)  # TID bits 7-5 = 001
        self.assertEqual(descr.sid, 0x01)  # SID bits 3-1 = 001
        self.assertTrue(descr.d)
        self.assertEqual(descr.tl0picidx, 0x01)
        self.assertEqual(payload, b"\xAA")

    def test_pion_compat_flexible_layer_indice_picture_id(self) -> None:
        """Test compatibility with Pion test case: FlexibleLayerIndicePictureID"""
        # From Pion: []byte{0xB0, 0x02, 0x23, 0x01, 0xAA}
        # Expected: F=true, I=true, L=true, PictureID=0x02, TID=0x01, SID=0x01, D=true
        # Note: In flexible mode, there's no TL0PICIDX, payload starts after layer byte
        data = bytes([0xB0, 0x02, 0x23, 0x01, 0xAA])
        descr, payload = Vp9PayloadDescriptor.parse(data)

        self.assertTrue(descr.F)  # Flexible mode
        self.assertTrue(descr.I)
        self.assertTrue(descr.L)
        self.assertEqual(descr.picture_id, 0x02)
        self.assertEqual(descr.tid, 0x01)
        self.assertEqual(descr.sid, 0x01)
        self.assertTrue(descr.d)
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
        self.assertTrue(parsed.B)
        self.assertTrue(parsed.E)

        # First packet: B=1, E=0
        descr_first = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=True,
            end_of_frame=False,
        )
        data = bytes(descr_first)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertTrue(parsed.B)
        self.assertFalse(parsed.E)

        # Middle packet: B=0, E=0
        descr_middle = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=False,
            end_of_frame=False,
        )
        data = bytes(descr_middle)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertFalse(parsed.B)
        self.assertFalse(parsed.E)

        # Last packet: B=0, E=1
        descr_last = Vp9PayloadDescriptor(
            picture_id_present=True,
            picture_id=10,
            start_of_frame=False,
            end_of_frame=True,
        )
        data = bytes(descr_last)
        parsed, _ = Vp9PayloadDescriptor.parse(data)
        self.assertFalse(parsed.B)
        self.assertTrue(parsed.E)


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

