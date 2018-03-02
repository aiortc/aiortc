from unittest import TestCase

from aiortc.codecs.opus import OpusDecoder, OpusEncoder
from aiortc.mediastreams import AudioFrame


class OpusTest(TestCase):
    def test_decoder(self):
        decoder = OpusDecoder()
        frame = decoder.decode(b'\xfc\xff\xfe')
        self.assertEqual(frame.channels, 2)
        self.assertEqual(frame.data, b'\x00' * 4 * 160)

    def test_encoder(self):
        encoder = OpusEncoder()
        frame = AudioFrame(
            channels=2,
            data=b'\x00' * 4 * 160)
        data = encoder.encode(frame)
        self.assertEqual(data, b'\xfc\xff\xfe')
