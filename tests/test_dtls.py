import asyncio
import datetime
from unittest import TestCase
from unittest.mock import patch

from aiortc.rtcdtlstransport import (DtlsError, RTCCertificate,
                                     RTCDtlsFingerprint, RTCDtlsParameters,
                                     RTCDtlsTransport)
from aiortc.utils import first_completed

from .utils import dummy_transport_pair, load, run

RTP = load('rtp.bin')
RTCP = load('rtcp_sr.bin')


class DummyIceTransport:
    def __init__(self, connection, role):
        self._connection = connection
        self.role = role

    async def stop(self):
        await self._connection.close()


def dummy_ice_transport_pair(loss=0):
    transport1, transport2 = dummy_transport_pair(loss=loss)
    return (
        DummyIceTransport(transport1, 'controlling'),
        DummyIceTransport(transport2, 'controlled')
    )


class RTCCertificateTest(TestCase):
    def test_generate(self):
        certificate = RTCCertificate.generateCertificate()
        self.assertIsNotNone(certificate)

        expires = certificate.expires
        self.assertIsNotNone(expires)
        self.assertTrue(isinstance(expires, datetime.datetime))

        fingerprints = certificate.getFingerprints()
        self.assertEqual(len(fingerprints), 1)
        self.assertEqual(fingerprints[0].algorithm, 'sha-256')
        self.assertEqual(len(fingerprints[0].value), 95)


class RTCDtlsTransportTest(TestCase):
    @patch('aiortc.rtcdtlstransport.lib.SSL_CTX_use_certificate')
    def test_broken_ssl(self, mock_use_certificate):
        mock_use_certificate.return_value = 0
        certificate = RTCCertificate.generateCertificate()
        with self.assertRaises(DtlsError):
            RTCDtlsTransport(None, [certificate])

    def test_data(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        # send encypted data
        run(session1.data.send(b'ping'))
        data = run(session2.data.recv())
        self.assertEqual(data, b'ping')

        run(session2.data.send(b'pong'))
        data = run(session1.data.recv())
        self.assertEqual(data, b'pong')

        # shutdown
        run(session1.stop())
        run(asyncio.sleep(0.5))
        self.assertEqual(session1.state, 'closed')
        self.assertEqual(session2.state, 'closed')

        # try closing again
        run(session1.stop())
        run(session2.stop())

        # try receving after close
        with self.assertRaises(ConnectionError):
            run(session1.data.recv())

        # try sending after close
        with self.assertRaises(ConnectionError):
            run(session1.data.send(b'foo'))

    def test_rtp(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        # send RTP
        run(session1.rtp.send(RTP))
        data = run(session2.rtp.recv())
        self.assertEqual(data, RTP)

        # send RTCP
        run(session2.rtp.send(RTCP))
        data = run(session1.rtp.recv())
        self.assertEqual(data, RTCP)

        # shutdown
        run(session1.stop())
        run(asyncio.sleep(0.5))
        self.assertEqual(session1.state, 'closed')
        self.assertEqual(session2.state, 'closed')

        # try closing again
        run(session1.stop())
        run(session2.stop())

        # try receving after close
        with self.assertRaises(ConnectionError):
            run(session1.rtp.recv())

        # try sending after close
        with self.assertRaises(ConnectionError):
            run(session1.rtp.send(RTP))

    def test_abrupt_disconnect(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        # break one connection
        run(first_completed(
            session1.data.recv(),
            transport1.stop(),
        ))
        run(asyncio.sleep(0))
        self.assertEqual(session1.state, 'closed')

        # break other connection
        run(first_completed(
            session2.data.recv(),
            transport2.stop(),
        ))
        run(asyncio.sleep(0))
        self.assertEqual(session2.state, 'closed')

        # try closing again
        run(session1.stop())
        run(session2.stop())

    def test_bad_client_fingerprint(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        bogus_parameters = RTCDtlsParameters(
            fingerprints=[RTCDtlsFingerprint(algorithm='sha-256', value='bogus_fingerprint')])
        with self.assertRaises(DtlsError) as cm:
            run(asyncio.gather(
                session1.start(bogus_parameters),
                session2.start(session1.getLocalParameters())))
        self.assertEqual(str(cm.exception), 'DTLS fingerprint does not match')
        self.assertEqual(session1.state, 'failed')
        self.assertEqual(session2.state, 'connecting')

        run(session1.stop())
        run(session2.stop())

    @patch('aiortc.rtcdtlstransport.lib.SSL_do_handshake')
    @patch('aiortc.rtcdtlstransport.lib.SSL_get_error')
    def test_handshake_error(self, mock_get_error, mock_do_handshake):
        mock_get_error.return_value = 1
        mock_do_handshake.return_value = -1

        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        with self.assertRaises(DtlsError) as cm:
            run(asyncio.gather(
                session1.start(session2.getLocalParameters()),
                session2.start(session1.getLocalParameters())))
        self.assertEqual(str(cm.exception), 'DTLS handshake failed (error 1)')
        self.assertEqual(session1.state, 'failed')
        self.assertEqual(session2.state, 'failed')

        run(session1.stop())
        run(session2.stop())

    def test_lossy_channel(self):
        transport1, transport2 = dummy_ice_transport_pair(loss=0.3)

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        run(session1.stop())
        run(session2.stop())
