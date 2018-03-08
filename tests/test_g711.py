from unittest import TestCase

from aiortc.codecs import PCMA_CODEC, PCMU_CODEC, get_decoder, get_encoder
from aiortc.codecs.g711 import (PcmaDecoder, PcmaEncoder, PcmuDecoder,
                                PcmuEncoder)
from aiortc.mediastreams import AudioFrame


class PcmaTest(TestCase):
    def test_decoder(self):
        decoder = get_decoder(PCMA_CODEC)
        self.assertTrue(isinstance(decoder, PcmaDecoder))

        frame = decoder.decode(b'\xd5' * 160)
        self.assertEqual(frame.channels, 1)
        self.assertEqual(frame.data, b'\x08\x00' * 160)
        self.assertEqual(frame.sample_rate, 8000)

    def test_encoder_mono_8hz(self):
        encoder = get_encoder(PCMA_CODEC)
        self.assertTrue(isinstance(encoder, PcmaEncoder))

        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xd5' * 160)

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(PCMA_CODEC)
        self.assertTrue(isinstance(encoder, PcmaEncoder))

        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xd5' * 160)

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(PCMA_CODEC)
        self.assertTrue(isinstance(encoder, PcmaEncoder))

        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 960,
            sample_rate=48000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xd5' * 160)


class PcmuTest(TestCase):
    def test_decoder(self):
        decoder = get_decoder(PCMU_CODEC)
        self.assertTrue(isinstance(decoder, PcmuDecoder))

        frame = decoder.decode(b'\xff' * 160)
        self.assertEqual(frame.channels, 1)
        self.assertEqual(frame.data, b'\x00\x00' * 160)
        self.assertEqual(frame.sample_rate, 8000)

    def test_encoder_mono_8hz(self):
        encoder = get_encoder(PCMU_CODEC)
        self.assertTrue(isinstance(encoder, PcmuEncoder))

        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xff' * 160)

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(PCMU_CODEC)
        self.assertTrue(isinstance(encoder, PcmuEncoder))

        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xff' * 160)

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(PCMU_CODEC)
        self.assertTrue(isinstance(encoder, PcmuEncoder))

        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 960,
            sample_rate=48000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xff' * 160)
