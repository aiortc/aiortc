import fractions
import io
from contextlib import redirect_stderr
from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder, h264
from aiortc.codecs.h264 import H264Decoder, H264Encoder, H264PayloadDescriptor
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters
from av import Packet

from .codecs import CodecTestCase
from .utils import load

H264_CODEC = RTCRtpCodecParameters(
    mimeType="video/H264", clockRate=90000, payloadType=100
)


class FragmentedCodecContext:
    def __init__(self, orig):
        self.__orig = orig

    def encode(self, frame):
        packages = self.__orig.encode(frame)
        dummy = Packet()
        dummy.pts = packages[0].pts
        packages.append(dummy)
        return packages

    def __getattr__(self, name):
        return getattr(self.__orig, name)


class H264PayloadDescriptorTest(TestCase):
    def test_parse_empty(self):
        with self.assertRaises(ValueError) as cm:
            H264PayloadDescriptor.parse(b"")
        self.assertEqual(str(cm.exception), "NAL unit is too short")

    def test_parse_stap_a(self):
        payload = load("h264_0000.bin")
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, True)
        self.assertEqual(repr(descr), "H264PayloadDescriptor(FF=True)")
        self.assertEqual(rest[:4], b"\00\00\00\01")
        self.assertEqual(len(rest), 26)

    def test_parse_stap_a_truncated(self):
        payload = load("h264_0000.bin")

        with self.assertRaises(ValueError) as cm:
            H264PayloadDescriptor.parse(payload[0:1])
        self.assertEqual(str(cm.exception), "NAL unit is too short")

        with self.assertRaises(ValueError) as cm:
            H264PayloadDescriptor.parse(payload[0:2])
        self.assertEqual(str(cm.exception), "STAP-A length field is truncated")

        with self.assertRaises(ValueError) as cm:
            H264PayloadDescriptor.parse(payload[0:3])
        self.assertEqual(str(cm.exception), "STAP-A data is truncated")

    def test_parse_stap_b(self):
        with self.assertRaises(ValueError) as cm:
            H264PayloadDescriptor.parse(b"\x19\x00")
        self.assertEqual(str(cm.exception), "NAL unit type 25 is not supported")

    def test_parse_fu_a_1(self):
        payload = load("h264_0001.bin")
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, True)
        self.assertEqual(repr(descr), "H264PayloadDescriptor(FF=True)")
        self.assertEqual(rest[:4], b"\00\00\00\01")
        self.assertEqual(len(rest), 916)

    def test_parse_fu_a_2(self):
        payload = load("h264_0002.bin")
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, False)
        self.assertEqual(repr(descr), "H264PayloadDescriptor(FF=False)")
        self.assertNotEqual(rest[:4], b"\00\00\00\01")
        self.assertEqual(len(rest), 912)

    def test_parse_fu_a_truncated(self):
        with self.assertRaises(ValueError) as cm:
            H264PayloadDescriptor.parse(b"\x7c")
        self.assertEqual(str(cm.exception), "NAL unit is too short")

    def test_parse_nalu(self):
        payload = load("h264_0003.bin")
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, True)
        self.assertEqual(repr(descr), "H264PayloadDescriptor(FF=True)")
        self.assertEqual(rest[:4], b"\00\00\00\01")
        self.assertEqual(rest[4:], payload)
        self.assertEqual(len(rest), 564)


class H264Test(CodecTestCase):
    def test_decoder(self):
        decoder = get_decoder(H264_CODEC)
        self.assertIsInstance(decoder, H264Decoder)

        # decode junk
        with redirect_stderr(io.StringIO()):
            frames = decoder.decode(JitterFrame(data=b"123", timestamp=0))
        self.assertEqual(frames, [])

    def test_encoder(self):
        encoder = get_encoder(H264_CODEC)
        self.assertIsInstance(encoder, H264Encoder)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        packages, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(packages), 1)

    def test_encoder_large(self):
        encoder = get_encoder(H264_CODEC)
        self.assertIsInstance(encoder, H264Encoder)

        # first keyframe
        frame = self.create_video_frame(width=1280, height=720, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(payloads), 3)
        self.assertEqual(timestamp, 0)

        # delta frame
        frame = self.create_video_frame(width=1280, height=720, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertEqual(timestamp, 3000)

        # force keyframe
        frame = self.create_video_frame(width=1280, height=720, pts=6000)
        payloads, timestamp = encoder.encode(frame, force_keyframe=True)
        self.assertGreaterEqual(len(payloads), 3)
        self.assertEqual(timestamp, 6000)

    def test_encoder_pack(self):
        encoder = get_encoder(H264_CODEC)
        self.assertTrue(isinstance(encoder, H264Encoder))

        packet = self.create_packet(payload=bytes([0, 0, 1, 0]), pts=1)
        payloads, timestamp = encoder.pack(packet)
        self.assertEqual(payloads, [b"\x00"])
        self.assertEqual(timestamp, 90)

    def test_encoder_buffering(self):
        create_encoder_context = h264.create_encoder_context

        def mock_create_encoder_context(*args, **kwargs):
            codec, _ = create_encoder_context(*args, **kwargs)
            return FragmentedCodecContext(codec), True

        h264.create_encoder_context = mock_create_encoder_context
        try:
            encoder = get_encoder(H264_CODEC)
            self.assertIsInstance(encoder, H264Encoder)

            frame = self.create_video_frame(width=640, height=480, pts=0)
            packages, timestamp = encoder.encode(frame)
            self.assertEqual(len(packages), 0)

            frame = self.create_video_frame(width=640, height=480, pts=3000)
            packages, timestamp = encoder.encode(frame)
            self.assertGreaterEqual(len(packages), 1)
        finally:
            h264.create_encoder_context = create_encoder_context

    def test_encoder_target_bitrate(self):
        encoder = get_encoder(H264_CODEC)
        self.assertIsInstance(encoder, H264Encoder)
        self.assertEqual(encoder.target_bitrate, 1000000)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        packages, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(packages), 1)
        self.assertTrue(len(packages[0]) < 1300)
        self.assertEqual(timestamp, 0)

        # change target bitrate
        encoder.target_bitrate = 1200000
        self.assertEqual(encoder.target_bitrate, 1200000)

        frame = self.create_video_frame(width=640, height=480, pts=3000)
        packages, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(packages), 1)
        self.assertTrue(len(packages[0]) < 1300)
        self.assertEqual(timestamp, 3000)

    def test_roundtrip_1280_720(self):
        self.roundtrip_video(H264_CODEC, 1280, 720)

    def test_roundtrip_960_540(self):
        self.roundtrip_video(H264_CODEC, 960, 540)

    def test_roundtrip_640_480(self):
        self.roundtrip_video(H264_CODEC, 640, 480)

    def test_roundtrip_640_480_time_base(self):
        self.roundtrip_video(
            H264_CODEC, 640, 480, time_base=fractions.Fraction(1, 9000)
        )

    def test_roundtrip_320_240(self):
        self.roundtrip_video(H264_CODEC, 320, 240)

    def test_split_bitstream(self):
        # No start code
        packages = list(H264Encoder._split_bitstream(b"\x00\x00\x00\x00"))
        self.assertEqual(packages, [])

        # 3-byte start code
        packages = list(
            H264Encoder._split_bitstream(b"\x00\x00\x01\xff\x00\x00\x01\xfb")
        )
        self.assertEqual(packages, [b"\xff", b"\xfb"])

        # 4-byte start code
        packages = list(
            H264Encoder._split_bitstream(b"\x00\x00\x00\x01\xff\x00\x00\x00\x01\xfb")
        )
        self.assertEqual(packages, [b"\xff", b"\xfb"])

        # Multiple bytes in a packet
        packages = list(
            H264Encoder._split_bitstream(
                b"\x00\x00\x00\x01\xff\xab\xcd\x00\x00\x00\x01\xfb"
            )
        )
        self.assertEqual(packages, [b"\xff\xab\xcd", b"\xfb"])

        # Skip leading 0s
        packages = list(H264Encoder._split_bitstream(b"\x00\x00\x00\x01\xff"))
        self.assertEqual(packages, [b"\xff"])

        # Both leading and trailing 0s
        packages = list(
            H264Encoder._split_bitstream(
                b"\x00\x00\x00\x00\x00\x00\x01\xff\x00\x00\x00\x00\x00"
            )
        )
        self.assertEqual(packages, [b"\xff\x00\x00\x00\x00\x00"])

    def test_packetize_one_small(self):
        packages = [bytes([0xFF, 0xFF])]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertListEqual(packages, packetize_packages)

        packages = [bytes([0xFF]) * 1300]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertListEqual(packages, packetize_packages)

    def test_packetize_one_big(self):
        packages = [bytes([0xFF, 0xFF] * 1000)]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertEqual(len(packetize_packages), 2)
        self.assertEqual(packetize_packages[0][0] & 0x1F, 28)
        self.assertEqual(packetize_packages[1][0] & 0x1F, 28)

    def test_packetize_two_small(self):
        packages = [bytes([0x01, 0xFF]), bytes([0xFF, 0xFF])]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertEqual(len(packetize_packages), 1)
        self.assertEqual(packetize_packages[0][0] & 0x1F, 24)

    def test_packetize_multiple_small(self):
        packages = [bytes([0x01, 0xFF])] * 9
        packetize_packages = H264Encoder._packetize(packages)
        self.assertEqual(len(packetize_packages), 1)
        self.assertEqual(packetize_packages[0][0] & 0x1F, 24)

        packages = [bytes([0x01, 0xFF])] * 10
        packetize_packages = H264Encoder._packetize(packages)
        self.assertEqual(len(packetize_packages), 2)
        self.assertEqual(packetize_packages[0][0] & 0x1F, 24)
        self.assertEqual(packetize_packages[1], packages[-1])

    def test_frame_encoder(self):
        encoder = get_encoder(H264_CODEC)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        packages = list(encoder._encode_frame(frame, False))

        self.assertGreaterEqual(len(packages), 3)
        # first frame must have at least
        set(p[0] & 0x1F for p in packages).issuperset(
            {
                8,  # PPS (picture parameter set)
                7,  # SPS (session parameter set)
                5,  # IDR (aka key frame)
            }
        )

        frame = self.create_video_frame(width=640, height=480, pts=3000)
        packages = list(encoder._encode_frame(frame, False))
        self.assertGreaterEqual(len(packages), 1)

        # change resolution
        frame = self.create_video_frame(width=320, height=240, pts=6000)
        packages = list(encoder._encode_frame(frame, False))
        self.assertGreaterEqual(len(packages), 1)
