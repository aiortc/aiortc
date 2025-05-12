from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

BOGUS_CODEC = RTCRtpCodecParameters(
    mimeType="audio/bogus", clockRate=8000, channels=1, payloadType=0
)


class CodecsTest(TestCase):
    def test_get_decoder(self) -> None:
        with self.assertRaises(ValueError):
            get_decoder(BOGUS_CODEC)

    def test_get_encoder(self) -> None:
        with self.assertRaises(ValueError):
            get_encoder(BOGUS_CODEC)
