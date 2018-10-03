import fractions
import io
from contextlib import redirect_stderr
from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.h264 import H264Decoder, H264Encoder, H264PayloadDescriptor
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .codecs import CodecTestCase
from .utils import load

H264_CODEC = RTCRtpCodecParameters(name='H264', clockRate=90000)


class H264PayloadDescriptorTest(TestCase):
    def test_parse_stap_a(self):
        payload = load('h264_0000.bin')
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, True)
        self.assertEqual(repr(descr), 'H264PayloadDescriptor(FF=True)')
        self.assertEqual(rest[:4], b'\00\00\00\01')
        self.assertEqual(len(rest), 26)

    def test_parse_fu_a_1(self):
        payload = load('h264_0001.bin')
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, True)
        self.assertEqual(repr(descr), 'H264PayloadDescriptor(FF=True)')
        self.assertEqual(rest[:4], b'\00\00\00\01')
        self.assertEqual(len(rest), 916)

    def test_parse_fu_a_2(self):
        payload = load('h264_0002.bin')
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, False)
        self.assertEqual(repr(descr), 'H264PayloadDescriptor(FF=False)')
        self.assertNotEqual(rest[:4], b'\00\00\00\01')
        self.assertEqual(len(rest), 912)

    def test_parse_nalu(self):
        payload = load('h264_0003.bin')
        descr, rest = H264PayloadDescriptor.parse(payload)
        self.assertEqual(descr.first_fragment, True)
        self.assertEqual(repr(descr), 'H264PayloadDescriptor(FF=True)')
        self.assertEqual(rest[:4], b'\00\00\00\01')
        self.assertEqual(rest[4:], payload)
        self.assertEqual(len(rest), 564)


class H264Test(CodecTestCase):
    def test_decoder(self):
        decoder = get_decoder(H264_CODEC)
        self.assertTrue(isinstance(decoder, H264Decoder))

        # decode junk
        with redirect_stderr(io.StringIO()):
            frames = decoder.decode(JitterFrame(data=b'123', timestamp=0))
        self.assertEqual(frames, [])

    def test_encoder(self):
        encoder = get_encoder(H264_CODEC)
        self.assertTrue(isinstance(encoder, H264Encoder))

        frame = self.create_video_frame(width=640, height=480, pts=0)
        packages, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(packages), 1)

    def test_roundtrip_1280_720(self):
        self.roundtrip_video(H264_CODEC, 1280, 720)

    def test_roundtrip_960_540(self):
        self.roundtrip_video(H264_CODEC, 960, 540)

    def test_roundtrip_640_480(self):
        self.roundtrip_video(H264_CODEC, 640, 480)

    def test_roundtrip_640_480_time_base(self):
        self.roundtrip_video(H264_CODEC, 640, 480, time_base=fractions.Fraction(1, 9000))

    def test_roundtrip_320_240(self):
        self.roundtrip_video(H264_CODEC, 320, 240)

    def test_split_bitstream(self):
        packages = list(H264Encoder._split_bitstream(b'\00\00\01\ff\00\00\01\ff'))
        self.assertEqual(len(packages), 2)

        packages = list(H264Encoder._split_bitstream(b'\00\00\00\01\ff'))
        self.assertEqual(len(packages), 1)

        packages = list(H264Encoder._split_bitstream(b'\00\00\00\00\00\00\01\ff\00\00\00\00\00'))
        self.assertEqual(len(packages), 1)

    def test_packetize_one_small(self):
        packages = [bytes([0xff, 0xff])]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertListEqual(packages, packetize_packages)

        packages = [bytes([0xff])*1300]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertListEqual(packages, packetize_packages)

    def test_packetize_one_big(self):
        packages = [bytes([0xff, 0xff]*1000)]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertEqual(len(packetize_packages), 2)
        self.assertEqual(packetize_packages[0][0] & 0x1f, 28)
        self.assertEqual(packetize_packages[1][0] & 0x1f, 28)

    def test_packetize_two_small(self):
        packages = [bytes([0x01, 0xff]), bytes([0xff, 0xff])]
        packetize_packages = H264Encoder._packetize(packages)
        self.assertEqual(len(packetize_packages), 1)
        self.assertEqual(packetize_packages[0][0] & 0x1f, 24)

    def test_frame_encoder(self):
        encoder = get_encoder(H264_CODEC)

        frame = self.create_video_frame(width=640, height=480, pts=0)
        packages = list(encoder._encode_frame(frame, False))

        self.assertGreaterEqual(len(packages), 3)
        # first frame must have at least
        set(p[0] & 0x1f for p in packages).issuperset({
            8,  # PPS (picture parameter set)
            7,  # SPS (session parameter set)
            5,  # IDR (aka key frame)
        })

        frame = self.create_video_frame(width=640, height=480, pts=3000)
        packages = list(encoder._encode_frame(frame, False))
        self.assertGreaterEqual(len(packages), 1)

        # change resolution
        frame = self.create_video_frame(width=320, height=240, pts=6000)
        packages = list(encoder._encode_frame(frame, False))
        self.assertGreaterEqual(len(packages), 1)
