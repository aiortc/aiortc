import asyncio
import logging
from unittest import TestCase
from unittest.mock import patch

from aiortc.dtls import DtlsError, DtlsSrtpContext, RTCDtlsTransport
from aiortc.utils import first_completed

from .utils import dummy_transport_pair, load, run

RTP = load('rtp.bin')
RTCP = load('rtcp_sr.bin')


def dummy_ice_transport_pair():
    transport1, transport2 = dummy_transport_pair()
    transport1.ice_controlling = True
    transport2.ice_controlling = False
    return transport1, transport2


class DtlsSrtpTest(TestCase):
    @patch('aiortc.dtls.lib.SSL_CTX_use_certificate')
    def test_broken_ssl(self, mock_use_certificate):
        mock_use_certificate.return_value = 0
        with self.assertRaises(DtlsError):
            DtlsSrtpContext()

    def test_data(self):
        transport1, transport2 = dummy_ice_transport_pair()

        context1 = DtlsSrtpContext()
        session1 = RTCDtlsTransport(
            context=context1, transport=transport1)

        context2 = DtlsSrtpContext()
        session2 = RTCDtlsTransport(
            context=context2, transport=transport2)

        session1.remote_fingerprint = session2.local_fingerprint
        session2.remote_fingerprint = session1.local_fingerprint
        run(asyncio.gather(session1.connect(), session2.connect()))

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

        context1 = DtlsSrtpContext()
        session1 = RTCDtlsTransport(
            context=context1, transport=transport1)

        context2 = DtlsSrtpContext()
        session2 = RTCDtlsTransport(
            context=context2, transport=transport2)

        session1.remote_fingerprint = session2.local_fingerprint
        session2.remote_fingerprint = session1.local_fingerprint
        run(asyncio.gather(session1.connect(), session2.connect()))

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

        context1 = DtlsSrtpContext()
        session1 = RTCDtlsTransport(
            context=context1, transport=transport1)

        context2 = DtlsSrtpContext()
        session2 = RTCDtlsTransport(
            context=context2, transport=transport2)

        session1.remote_fingerprint = session2.local_fingerprint
        session2.remote_fingerprint = session1.local_fingerprint
        run(asyncio.gather(session1.connect(), session2.connect()))

        # break one connection
        run(first_completed(
            session1.data.recv(),
            transport1.close(),
        ))
        run(asyncio.sleep(0))
        self.assertEqual(session1.state, 'closed')

        # break other connection
        run(first_completed(
            session2.data.recv(),
            transport2.close(),
        ))
        run(asyncio.sleep(0))
        self.assertEqual(session2.state, 'closed')

        # try closing again
        run(session1.stop())
        run(session2.stop())

    def test_bad_client_fingerprint(self):
        transport1, transport2 = dummy_ice_transport_pair()

        context1 = DtlsSrtpContext()
        session1 = RTCDtlsTransport(
            context=context1, transport=transport1)

        context2 = DtlsSrtpContext()
        session2 = RTCDtlsTransport(
            context=context2, transport=transport2)

        session1.remote_fingerprint = 'bogus_fingerprint'
        session2.remote_fingerprint = session1.local_fingerprint
        with self.assertRaises(DtlsError) as cm:
            run(asyncio.gather(session1.connect(), session2.connect()))
        self.assertEqual(str(cm.exception), 'DTLS fingerprint does not match')
        self.assertEqual(session1.state, 'failed')
        self.assertEqual(session2.state, 'connecting')

        run(session1.stop())
        run(session2.stop())

    @patch('aiortc.dtls.lib.SSL_do_handshake')
    @patch('aiortc.dtls.lib.SSL_get_error')
    def test_handshake_error(self, mock_get_error, mock_do_handshake):
        mock_get_error.return_value = 1
        mock_do_handshake.return_value = -1

        transport1, transport2 = dummy_ice_transport_pair()

        context1 = DtlsSrtpContext()
        session1 = RTCDtlsTransport(
            context=context1, transport=transport1)

        context2 = DtlsSrtpContext()
        session2 = RTCDtlsTransport(
            context=context2, transport=transport2)

        session1.remote_fingerprint = session2.local_fingerprint
        session2.remote_fingerprint = session1.local_fingerprint
        with self.assertRaises(DtlsError) as cm:
            run(asyncio.gather(session1.connect(), session2.connect()))
        self.assertEqual(str(cm.exception), 'DTLS handshake failed (error 1)')
        self.assertEqual(session1.state, 'failed')
        self.assertEqual(session2.state, 'failed')

        run(session1.stop())
        run(session2.stop())


logging.basicConfig(level=logging.DEBUG)
