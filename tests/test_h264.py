import io
from unittest import TestCase
from contextlib import redirect_stderr

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.h264 import H264PayloadDescriptor, H264Decoder, H264Encoder
from aiortc.mediastreams import VideoFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters
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


class H264Test(TestCase):
    def test_decoder(self):
        decoder = get_decoder(H264_CODEC)
        self.assertTrue(isinstance(decoder, H264Decoder))

        # decode junk
        with redirect_stderr(io.StringIO()):
            frames = decoder.decode(b'123')
        self.assertEqual(frames, [])

    def test_encoder(self):
        encoder = get_encoder(H264_CODEC)
        self.assertTrue(isinstance(encoder, H264Encoder))

        frame = VideoFrame(width=640, height=480)
        packages = encoder.encode(frame)
        self.assertGreaterEqual(len(packages), 1)

    def roundtrip(self, width, height):
        """
        Round-trip a VideoFrame through encoder then decoder.
        """
        encoder = get_encoder(H264_CODEC)
        decoder = get_decoder(H264_CODEC)

        # encode
        frame = VideoFrame(width=width, height=height)
        packages = encoder.encode(frame)

        # depacketize
        data = b''
        for package in packages:
            descriptor, package_data = H264PayloadDescriptor.parse(package)
            data += package_data

        # decode
        frames = decoder.decode(data)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].width, width)
        self.assertEqual(frames[0].height, height)

    def test_roundtrip_640_480(self):
        self.roundtrip(640, 480)

    def test_roundtrip_320_240(self):
        self.roundtrip(320, 240)

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

        frame = VideoFrame(width=640, height=480)
        packages = list(encoder._encode_frame(frame, False))

        self.assertGreaterEqual(len(packages), 3)
        # first frame must have at least
        set(p[0] & 0x1f for p in packages).issuperset({
            8,  # PPS (picture parameter set)
            7,  # SPS (session parameter set)
            5,  # IDR (aka key frame)
        })

        frame = VideoFrame(width=640, height=480)
        packages = list(encoder._encode_frame(frame, False))
        self.assertGreaterEqual(len(packages), 1)

        with redirect_stderr(io.StringIO()):
            # should discart corrupted frame
            frame = VideoFrame(width=320, height=240)
            packages = list(encoder._encode_frame(frame, False))
            self.assertGreaterEqual(len(packages), 0)
