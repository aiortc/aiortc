from unittest import TestCase

from aiortc.codecs.opus import OpusDecoder, OpusEncoder
from aiortc.mediastreams import AudioFrame


class OpusTest(TestCase):
    def test_decoder(self):
        decoder = OpusDecoder()
        frame = decoder.decode(b'\xfc\xff\xfe')
        self.assertEqual(frame.channels, 2)
        self.assertEqual(frame.data, b'\x00' * 4 * 960)
        self.assertEqual(frame.sample_rate, 48000)

    def test_encoder_mono_8khz(self):
        encoder = OpusEncoder()
        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xfc\xff\xfe')

    def test_encoder_stereo_8khz(self):
        encoder = OpusEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 160,
            sample_rate=8000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xfc\xff\xfe')

    def test_encoder_stereo_48khz(self):
        encoder = OpusEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 960,
            sample_rate=48000)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xfc\xff\xfe')
