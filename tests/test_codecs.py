from unittest import TestCase

from vsaiortc.codecs import get_decoder, get_encoder
from vsaiortc.rtcrtpparameters import RTCRtpCodecParameters

BOGUS_CODEC = RTCRtpCodecParameters(
    mimeType="audio/bogus", clockRate=8000, channels=1, payloadType=0
)


class CodecsTest(TestCase):
    def test_get_decoder(self):
        with self.assertRaises(ValueError):
            get_decoder(BOGUS_CODEC)

    def test_get_encoder(self):
        with self.assertRaises(ValueError):
            get_encoder(BOGUS_CODEC)
