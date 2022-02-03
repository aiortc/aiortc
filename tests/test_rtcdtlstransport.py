import asyncio
import datetime
from unittest import TestCase
from unittest.mock import patch

from aiortc.rtcdtlstransport import (
    DtlsError,
    RTCCertificate,
    RTCDtlsFingerprint,
    RTCDtlsParameters,
    RTCDtlsTransport,
    RtpRouter,
)
from aiortc.rtcrtpparameters import (
    RTCRtpCodecParameters,
    RTCRtpDecodingParameters,
    RTCRtpReceiveParameters,
)
from aiortc.rtp import (
    RTCP_PSFB_APP,
    RTCP_PSFB_PLI,
    RTCP_RTPFB_NACK,
    RtcpByePacket,
    RtcpPsfbPacket,
    RtcpReceiverInfo,
    RtcpRrPacket,
    RtcpRtpfbPacket,
    RtcpSenderInfo,
    RtcpSrPacket,
    RtpPacket,
    pack_remb_fci,
)

from .utils import asynctest, dummy_ice_transport_pair, load

RTP = load("rtp.bin")
RTCP = load("rtcp_sr.bin")


class BrokenDataReceiver:
    def __init__(self):
        self.data = []

    async def _handle_data(self, data):
        raise Exception("some error")


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
        self.assertEqual(fingerprints[0].algorithm, "sha-256")
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

    @patch("aiortc.rtcdtlstransport.lib.SSL_CTX_use_certificate")
    @asynctest
    async def test_broken_ssl(self, mock_use_certificate):
        mock_use_certificate.return_value = 0

        transport1, transport2 = dummy_ice_transport_pair()

        certificate = RTCCertificate.generateCertificate()
        with self.assertRaises(DtlsError):
            RTCDtlsTransport(transport1, [certificate])

    @asynctest
    async def test_data(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])
        receiver1 = DummyDataReceiver()
        session1._register_data_receiver(receiver1)

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        receiver2 = DummyDataReceiver()
        session2._register_data_receiver(receiver2)

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )

        # send encypted data
        await session1._send_data(b"ping")
        await asyncio.sleep(0.1)
        self.assertEqual(receiver2.data, [b"ping"])

        await session2._send_data(b"pong")
        await asyncio.sleep(0.1)
        self.assertEqual(receiver1.data, [b"pong"])

        # shutdown
        await session1.stop()
        await asyncio.sleep(0.1)
        self.assertEqual(session1.state, "closed")
        self.assertEqual(session2.state, "closed")

        # try closing again
        await session1.stop()
        await session2.stop()

        # try sending after close
        with self.assertRaises(ConnectionError):
            await session1._send_data(b"foo")

    @asynctest
    async def test_data_handler_error(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])
        receiver1 = DummyDataReceiver()
        session1._register_data_receiver(receiver1)

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        session2._register_data_receiver(BrokenDataReceiver())

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )

        # send encypted data
        await session1._send_data(b"ping")
        await asyncio.sleep(0.1)

        # shutdown
        await session1.stop()
        await session2.stop()

    @asynctest
    async def test_rtp(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])
        receiver1 = DummyRtpReceiver()
        session1._register_rtp_receiver(
            receiver1,
            RTCRtpReceiveParameters(
                codecs=[
                    RTCRtpCodecParameters(
                        mimeType="audio/PCMU", clockRate=8000, payloadType=0
                    )
                ],
                encodings=[RTCRtpDecodingParameters(ssrc=1831097322, payloadType=0)],
            ),
        )

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        receiver2 = DummyRtpReceiver()
        session2._register_rtp_receiver(
            receiver2,
            RTCRtpReceiveParameters(
                codecs=[
                    RTCRtpCodecParameters(
                        mimeType="audio/PCMU", clockRate=8000, payloadType=0
                    )
                ],
                encodings=[RTCRtpDecodingParameters(ssrc=4028317929, payloadType=0)],
            ),
        )

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )
        self.assertCounters(session1, session2, 2, 2)

        # send RTP
        await session1._send_rtp(RTP)
        await asyncio.sleep(0.1)
        self.assertCounters(session1, session2, 3, 2)
        self.assertEqual(len(receiver2.rtcp_packets), 0)
        self.assertEqual(len(receiver2.rtp_packets), 1)

        # send RTCP
        await session2._send_rtp(RTCP)
        await asyncio.sleep(0.1)
        self.assertCounters(session1, session2, 3, 3)
        self.assertEqual(len(receiver1.rtcp_packets), 1)
        self.assertEqual(len(receiver1.rtp_packets), 0)

        # shutdown
        await session1.stop()
        await asyncio.sleep(0.1)
        self.assertCounters(session1, session2, 4, 3)
        self.assertEqual(session1.state, "closed")
        self.assertEqual(session2.state, "closed")

        # try closing again
        await session1.stop()
        await session2.stop()

        # try sending after close
        with self.assertRaises(ConnectionError):
            await session1._send_rtp(RTP)

    @asynctest
    async def test_rtp_malformed(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        # receive truncated RTP
        await session1._handle_rtp_data(RTP[0:8], 0)

        # receive truncated RTCP
        await session1._handle_rtcp_data(RTCP[0:8])

    @asynctest
    async def test_srtp_unprotect_error(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])
        receiver1 = DummyRtpReceiver()
        session1._register_rtp_receiver(
            receiver1,
            RTCRtpReceiveParameters(
                codecs=[
                    RTCRtpCodecParameters(
                        mimeType="audio/PCMU", clockRate=8000, payloadType=0
                    )
                ],
                encodings=[RTCRtpDecodingParameters(ssrc=1831097322, payloadType=0)],
            ),
        )

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])
        receiver2 = DummyRtpReceiver()
        session2._register_rtp_receiver(
            receiver2,
            RTCRtpReceiveParameters(
                codecs=[
                    RTCRtpCodecParameters(
                        mimeType="audio/PCMU", clockRate=8000, payloadType=0
                    )
                ],
                encodings=[RTCRtpDecodingParameters(ssrc=4028317929, payloadType=0)],
            ),
        )

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )

        # send same RTP twice, to trigger error on the receiver side:
        # "replay check failed (bad index)"
        await session1._send_rtp(RTP)
        await session1._send_rtp(RTP)
        await asyncio.sleep(0.1)
        self.assertEqual(len(receiver2.rtcp_packets), 0)
        self.assertEqual(len(receiver2.rtp_packets), 1)

        # shutdown
        await session1.stop()
        await session2.stop()

    @asynctest
    async def test_abrupt_disconnect(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )

        # break connections -> tasks exits
        await transport1.stop()
        await transport2.stop()
        await asyncio.sleep(0.1)

        # close DTLS
        await session1.stop()
        await session2.stop()

        # check outcome
        self.assertEqual(session1.state, "closed")
        self.assertEqual(session2.state, "closed")

    @asynctest
    async def test_abrupt_disconnect_2(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )

        def fake_write_ssl():
            raise ConnectionError

        session1._write_ssl = fake_write_ssl

        # close DTLS -> ConnectionError
        await session1.stop()
        await session2.stop()
        await asyncio.sleep(0.1)

        # check outcome
        self.assertEqual(session1.state, "closed")
        self.assertEqual(session2.state, "closed")

    @asynctest
    async def test_bad_client_fingerprint(self):
        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        bogus_parameters = RTCDtlsParameters(
            fingerprints=[
                RTCDtlsFingerprint(algorithm="sha-256", value="bogus_fingerprint")
            ]
        )
        await asyncio.gather(
            session1.start(bogus_parameters),
            session2.start(session1.getLocalParameters()),
        )
        self.assertEqual(session1.state, "failed")
        self.assertEqual(session2.state, "connected")

        await session1.stop()
        await session2.stop()

    @patch("aiortc.rtcdtlstransport.lib.SSL_do_handshake")
    @patch("aiortc.rtcdtlstransport.lib.SSL_get_error")
    @patch("aiortc.rtcdtlstransport.lib.ERR_get_error")
    @asynctest
    async def test_handshake_error(
        self, mock_err_get_error, mock_ssl_get_error, mock_do_handshake
    ):
        mock_err_get_error.side_effect = [0x2006D080, 0, 0]
        mock_ssl_get_error.return_value = 1
        mock_do_handshake.return_value = -1

        transport1, transport2 = dummy_ice_transport_pair()

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )
        self.assertEqual(session1.state, "failed")
        self.assertEqual(session2.state, "failed")

        await session1.stop()
        await session2.stop()

    @asynctest
    async def test_lossy_channel(self):
        """
        Transport with 25% loss eventually connects.
        """
        transport1, transport2 = dummy_ice_transport_pair()
        loss_pattern = [True, False, False, False]
        transport1._connection.loss_pattern = loss_pattern
        transport2._connection.loss_pattern = loss_pattern

        certificate1 = RTCCertificate.generateCertificate()
        session1 = RTCDtlsTransport(transport1, [certificate1])

        certificate2 = RTCCertificate.generateCertificate()
        session2 = RTCDtlsTransport(transport2, [certificate2])

        await asyncio.gather(
            session1.start(session2.getLocalParameters()),
            session2.start(session1.getLocalParameters()),
        )

        await session1.stop()
        await session2.stop()


class RtpRouterTest(TestCase):
    def test_route_rtcp(self):
        receiver = object()
        sender = object()

        router = RtpRouter()
        router.register_receiver(receiver, ssrcs=[1234, 2345], payload_types=[96, 97])
        router.register_sender(sender, ssrc=3456)

        # BYE
        packet = RtcpByePacket(sources=[1234, 2345])
        self.assertEqual(router.route_rtcp(packet), set([receiver]))

        # RR
        packet = RtcpRrPacket(
            ssrc=1234,
            reports=[
                RtcpReceiverInfo(
                    ssrc=3456,
                    fraction_lost=0,
                    packets_lost=0,
                    highest_sequence=630,
                    jitter=1906,
                    lsr=0,
                    dlsr=0,
                )
            ],
        )
        self.assertEqual(router.route_rtcp(packet), set([sender]))

        # SR
        packet = RtcpSrPacket(
            ssrc=1234,
            sender_info=RtcpSenderInfo(
                ntp_timestamp=0, rtp_timestamp=0, packet_count=0, octet_count=0
            ),
            reports=[
                RtcpReceiverInfo(
                    ssrc=3456,
                    fraction_lost=0,
                    packets_lost=0,
                    highest_sequence=630,
                    jitter=1906,
                    lsr=0,
                    dlsr=0,
                )
            ],
        )
        self.assertEqual(router.route_rtcp(packet), set([receiver, sender]))

        # PSFB - PLI
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_PLI, ssrc=1234, media_ssrc=3456)
        self.assertEqual(router.route_rtcp(packet), set([sender]))

        # PSFB - REMB
        packet = RtcpPsfbPacket(
            fmt=RTCP_PSFB_APP,
            ssrc=1234,
            media_ssrc=0,
            fci=pack_remb_fci(4160000, [3456]),
        )
        self.assertEqual(router.route_rtcp(packet), set([sender]))

        # PSFB - JUNK
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_APP, ssrc=1234, media_ssrc=0, fci=b"JUNK")
        self.assertEqual(router.route_rtcp(packet), set())

        # RTPFB
        packet = RtcpRtpfbPacket(fmt=RTCP_RTPFB_NACK, ssrc=1234, media_ssrc=3456)
        self.assertEqual(router.route_rtcp(packet), set([sender]))

    def test_route_rtp(self):
        receiver1 = object()
        receiver2 = object()

        router = RtpRouter()
        router.register_receiver(receiver1, ssrcs=[1234, 2345], payload_types=[96, 97])
        router.register_receiver(receiver2, ssrcs=[3456, 4567], payload_types=[98, 99])

        # known SSRC and payload type
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=1234, payload_type=96)), receiver1
        )
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=2345, payload_type=97)), receiver1
        )
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=3456, payload_type=98)), receiver2
        )
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=4567, payload_type=99)), receiver2
        )

        # unknown SSRC, known payload type
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=5678, payload_type=96)), receiver1
        )
        self.assertEqual(router.ssrc_table[5678], receiver1)

        # unknown SSRC and payload type
        self.assertEqual(router.route_rtp(RtpPacket(ssrc=6789, payload_type=100)), None)

    def test_route_rtp_ambiguous_payload_type(self):
        receiver1 = object()
        receiver2 = object()

        router = RtpRouter()
        router.register_receiver(receiver1, ssrcs=[1234, 2345], payload_types=[96, 97])
        router.register_receiver(receiver2, ssrcs=[3456, 4567], payload_types=[96, 97])

        # known SSRC and payload type
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=1234, payload_type=96)), receiver1
        )
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=2345, payload_type=97)), receiver1
        )
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=3456, payload_type=96)), receiver2
        )
        self.assertEqual(
            router.route_rtp(RtpPacket(ssrc=4567, payload_type=97)), receiver2
        )

        # unknown SSRC, ambiguous payload type
        self.assertEqual(router.route_rtp(RtpPacket(ssrc=5678, payload_type=96)), None)
        self.assertEqual(router.route_rtp(RtpPacket(ssrc=5678, payload_type=97)), None)
