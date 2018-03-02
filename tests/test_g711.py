from unittest import TestCase

from aiortc.codecs.g711 import PcmaDecoder, PcmaEncoder, PcmuDecoder, PcmuEncoder
from aiortc.mediastreams import AudioFrame


class PcmaTestCase(TestCase):
    def test_decoder(self):
        decoder = PcmaDecoder()
        frame = decoder.decode(b'\xd5' * 160)
        self.assertEqual(frame.channels, 1)
        self.assertEqual(frame.data, b'\x08\x00' * 160)
        self.assertEqual(frame.sample_rate, 8000)

    def test_encoder_mono_8hz(self):
        encoder = PcmaEncoder()
        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xd5' * 160)

    def test_encoder_stereo_8khz(self):
        encoder = PcmaEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xd5' * 160)

    def test_encoder_stereo_48khz(self):
        encoder = PcmaEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 960,
            sample_rate=48000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xd5' * 160)


class PcmuTestCase(TestCase):
    def test_decoder(self):
        decoder = PcmuDecoder()
        frame = decoder.decode(b'\xff' * 160)
        self.assertEqual(frame.channels, 1)
        self.assertEqual(frame.data, b'\x00\x00' * 160)
        self.assertEqual(frame.sample_rate, 8000)

    def test_encoder_mono_8hz(self):
        encoder = PcmuEncoder()
        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xff' * 160)

    def test_encoder_stereo_8khz(self):
        encoder = PcmuEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xff' * 160)

    def test_encoder_stereo_48khz(self):
        encoder = PcmuEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 960,
            sample_rate=48000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xff' * 160)
