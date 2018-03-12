import asyncio
from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcrtpsender import RTCRtpSender

from .utils import dummy_dtls_transport_pair, run


class ClosedDtlsTransport:
    state = 'closed'


class RTCRtpSenderTest(TestCase):
    def test_connection_error(self):
        transport, _ = dummy_dtls_transport_pair()

        sender = RTCRtpSender(AudioStreamTrack(), transport)
        self.assertEqual(sender.transport, transport)

        run(asyncio.gather(
            sender._run(codec=PCMU_CODEC),
            transport.close()))

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpSender('audio', dtlsTransport)
