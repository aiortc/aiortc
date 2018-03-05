import asyncio
from unittest import TestCase

from aiortc.codecs.g711 import PcmuDecoder, PcmuEncoder
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcrtptransceiver import RTCRtpReceiver, RTCRtpSender

from .utils import dummy_transport_pair, run


class RTCRtpReceiverTest(TestCase):
    def test_connection_error(self):
        transport, _ = dummy_transport_pair()
        decoder = PcmuDecoder()

        receiver = RTCRtpReceiver()
        run(asyncio.gather(
            receiver._run(transport=transport, decoder=decoder, payload_type=0),
            transport.close()))


class RTCRtpSenderTest(TestCase):
    def test_connection_error(self):
        transport, _ = dummy_transport_pair()
        encoder = PcmuEncoder()

        sender = RTCRtpSender()
        sender._track = AudioStreamTrack()
        run(asyncio.gather(
            sender._run(transport=transport, encoder=encoder, payload_type=0),
            transport.close()))
