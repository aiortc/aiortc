import asyncio
import datetime
from unittest import TestCase
from unittest.mock import patch

from aiortc.rtcdtlstransport import (DtlsError, RTCCertificate,
                                     RTCDtlsFingerprint, RTCDtlsParameters,
                                     RTCDtlsTransport)
from aiortc.rtcrtpparameters import RTCRtcpParameters, RTCRtpParameters

from .utils import dummy_ice_transport_pair, load, run

RTP = load('rtp.bin')
RTCP = load('rtcp_sr.bin')


class DummyDataReceiver:
    def __init__(self):
        self.data = []

    async def _handle_data(self, data):
        self.data.append(data)


class DummyRtpReceiver:
    def __init__(self):
        self.rtp_packets = []
        self.rtcp_packets = []

    def _handle_disconnect(self):
        pass

    async def _handle_rtp_packet(self, packet, arrival_time_ms):
        self.rtp_packets.append(packet)

    async def _handle_rtcp_packet(self, packet):
        self.rtcp_packets.append(packet)


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
    def assertCounters(self, transport_a, transport_b, packets_sent_a, packets_sent_b):
        stats_a = transport_a._get_stats()[transport_a._stats_id]
        stats_b = transport_b._get_stats()[transport_b._stats_id]

        self.assertEqual(stats_a.packetsSent, packets_sent_a)
        self.assertEqual(stats_a.packetsReceived, packets_sent_b)
        self.assertGreater(stats_a.bytesSent, 0)
        self.assertGreater(stats_a.bytesReceived, 0)

        self.assertEqual(stats_b.packetsSent, packets_sent_b)
        self.assertEqual(stats_b.packetsReceived, packets_sent_a)
        self.assertGreater(stats_b.bytesSent, 0)
        self.assertGreater(stats_b.bytesReceived, 0)

        self.assertEqual(stats_a.bytesSent, stats_b.bytesReceived)
        self.assertEqual(stats_b.bytesSent, stats_a.bytesReceived)

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
        receiver1 = DummyDataReceiver()
        session1._register_data_receiver(receiver1)

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        receiver2 = DummyDataReceiver()
        session2._register_data_receiver(receiver2)

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        # send encypted data
        run(session1._send_data(b'ping'))
        run(asyncio.sleep(0.1))
        self.assertEqual(receiver2.data, [b'ping'])

        run(session2._send_data(b'pong'))
        run(asyncio.sleep(0.1))
        self.assertEqual(receiver1.data, [b'pong'])

        # shutdown
        run(session1.stop())
        run(asyncio.sleep(0.5))
        self.assertEqual(session1.state, 'closed')
        self.assertEqual(session2.state, 'closed')

        # try closing again
        run(session1.stop())
        run(session2.stop())

        # try sending after close
        with self.assertRaises(ConnectionError):
            run(session1._send_data(b'foo'))

    def test_rtp(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])
        receiver1 = DummyRtpReceiver()
        session1._register_rtp_receiver(receiver1, RTCRtpParameters(
            rtcp=RTCRtcpParameters(ssrc=1831097322)))

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        receiver2 = DummyRtpReceiver()
        session2._register_rtp_receiver(receiver2, RTCRtpParameters(
            rtcp=RTCRtcpParameters(ssrc=4028317929)))

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))
        self.assertCounters(session1, session2, 2, 2)

        # send RTP
        run(session1._send_rtp(RTP))
        run(asyncio.sleep(0.1))
        self.assertCounters(session1, session2, 3, 2)
        self.assertEqual(len(receiver2.rtcp_packets), 0)
        self.assertEqual(len(receiver2.rtp_packets), 1)

        # send RTCP
        run(session2._send_rtp(RTCP))
        run(asyncio.sleep(0.1))
        self.assertCounters(session1, session2, 3, 3)
        self.assertEqual(len(receiver1.rtcp_packets), 1)
        self.assertEqual(len(receiver1.rtp_packets), 0)

        # shutdown
        run(session1.stop())
        run(asyncio.sleep(0.5))
        self.assertCounters(session1, session2, 4, 3)
        self.assertEqual(session1.state, 'closed')
        self.assertEqual(session2.state, 'closed')

        # try closing again
        run(session1.stop())
        run(session2.stop())

        # try sending after close
        with self.assertRaises(ConnectionError):
            run(session1._send_rtp(RTP))

    def test_srtp_unprotect_error(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])
        receiver1 = DummyRtpReceiver()
        session1._register_rtp_receiver(receiver1, RTCRtpParameters(
            rtcp=RTCRtcpParameters(ssrc=1831097322)))

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        receiver2 = DummyRtpReceiver()
        session2._register_rtp_receiver(receiver2, RTCRtpParameters(
            rtcp=RTCRtcpParameters(ssrc=4028317929)))

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        # send same RTP twice, to trigger error on the receiver side:
        # "replay check failed (bad index)"
        run(session1._send_rtp(RTP))
        run(session1._send_rtp(RTP))
        run(asyncio.sleep(0.5))
        self.assertEqual(len(receiver2.rtcp_packets), 0)
        self.assertEqual(len(receiver2.rtp_packets), 1)

        # shutdown
        run(session1.stop())
        run(session2.stop())

    def test_abrupt_disconnect(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        # break connections
        run(transport1.stop())
        run(transport2.stop())

        # close DTLS -> raises ConnectionError
        run(session1.stop())
        run(session2.stop())

        # check outcome
        self.assertEqual(session1.state, 'closed')
        self.assertEqual(session2.state, 'closed')

    def test_bad_client_fingerprint(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        bogus_parameters = RTCDtlsParameters(
            fingerprints=[RTCDtlsFingerprint(algorithm='sha-256', value='bogus_fingerprint')])
        run(asyncio.gather(
            session1.start(bogus_parameters),
            session2.start(session1.getLocalParameters())))
        self.assertEqual(session1.state, 'failed')
        self.assertEqual(session2.state, 'connected')

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

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))
        self.assertEqual(session1.state, 'failed')
        self.assertEqual(session2.state, 'failed')

        run(session1.stop())
        run(session2.stop())

    def test_lossy_channel(self):
        """
        Transport with 25% loss eventually connects.
        """
        transport1, transport2 = dummy_ice_transport_pair(loss=[True, False, False, False])

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        run(asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters())))

        run(session1.stop())
        run(session2.stop())
