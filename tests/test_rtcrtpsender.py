import asyncio
from unittest import TestCase

from aiortc.codecs.g711 import PcmuEncoder
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcrtpsender import RTCRtpSender

from .utils import dummy_dtls_transport_pair, run


class RTCRtpSenderTest(TestCase):
    def test_connection_error(self):
        transport, _ = dummy_dtls_transport_pair()
        encoder = PcmuEncoder()

        sender = RTCRtpSender(AudioStreamTrack())
        self.assertEqual(sender.transport, None)

        run(asyncio.gather(
            sender._run(transport=transport, encoder=encoder, payload_type=0),
            transport.close()))
