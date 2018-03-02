from unittest import TestCase

from aiortc.codecs.opus import OpusEncoder
from aiortc.mediastreams import AudioFrame


class OpusTest(TestCase):
    def test_encode(self):
        frame = AudioFrame(
            channels=2,
            data=b'\x00' * 4 * 160)
        encoder = OpusEncoder()
        output = encoder.encode(frame)
        self.assertEqual(output, b'\xfc\xff\xfe')
