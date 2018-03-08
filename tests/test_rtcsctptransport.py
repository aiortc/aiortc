from unittest import TestCase

from aiortc.exceptions import InvalidStateError
from aiortc.rtcsctptransport import RTCSctpTransport


class FakeDtlsTransport:
    state = 'new'


class RTCSctpTransportTest(TestCase):
    def test_construct(self):
        dtlsTransport = FakeDtlsTransport()
        sctpTransport = RTCSctpTransport(dtlsTransport)
        self.assertEqual(sctpTransport.transport, dtlsTransport)
        self.assertEqual(sctpTransport.port, 5000)

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = FakeDtlsTransport()
        dtlsTransport.state = 'closed'
        with self.assertRaises(InvalidStateError):
            RTCSctpTransport(dtlsTransport)
