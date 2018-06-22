from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcrtpparameters import RTCRtpParameters
from aiortc.rtcrtpsender import RTCRtpSender

from .utils import dummy_dtls_transport_pair, run


class ClosedDtlsTransport:
    state = 'closed'


class RTCRtpSenderTest(TestCase):
    def test_construct(self):
        transport, _ = dummy_dtls_transport_pair()

        sender = RTCRtpSender('audio', transport)
        self.assertEqual(sender.kind, 'audio')
        self.assertEqual(sender.transport, transport)

    def test_construct_invalid_dtls_transport_state(self):
        transport = ClosedDtlsTransport()

        with self.assertRaises(InvalidStateError):
            RTCRtpSender('audio', transport)

    def test_connection_error(self):
        """
        Close the underlying transport before the sender.
        """
        transport, _ = dummy_dtls_transport_pair()

        sender = RTCRtpSender(AudioStreamTrack(), transport)
        self.assertEqual(sender.kind, 'audio')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[PCMU_CODEC])))

        run(transport.close())

    def test_stop(self):
        transport, _ = dummy_dtls_transport_pair()

        sender = RTCRtpSender(AudioStreamTrack(), transport)
        self.assertEqual(sender.kind, 'audio')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[PCMU_CODEC])))

        # clean shutdown
        run(sender.stop())

    def test_stop_before_send(self):
        transport, _ = dummy_dtls_transport_pair()
        sender = RTCRtpSender(AudioStreamTrack(), transport)
        run(sender.stop())
