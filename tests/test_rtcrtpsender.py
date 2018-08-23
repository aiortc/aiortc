import asyncio
from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpParameters
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.rtp import (RTCP_PSFB_PLI, RTCP_RTPFB_NACK, RtcpPacket,
                        RtcpPsfbPacket, RtcpRtpfbPacket, RtpPacket, is_rtcp,
                        seq_plus_one)
from aiortc.stats import RTCStatsReport

from .utils import dummy_dtls_transport_pair, load, run


class ClosedDtlsTransport:
    state = 'closed'


class FakeDtlsTransport:
    queue = asyncio.Queue(maxsize=1)
    state = 'connected'

    async def _send_rtp(self, data):
        if not is_rtcp(data):
            packet = RtpPacket.parse(data)
            await self.queue.put(packet)


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

        run(transport.stop())

    def test_handle_rtcp_nack(self):
        transport, remote = dummy_dtls_transport_pair()

        sender = RTCRtpSender(VideoStreamTrack(), transport)
        self.assertEqual(sender.kind, 'video')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP feedback NACK
        packet = RtcpRtpfbPacket(fmt=RTCP_RTPFB_NACK, ssrc=1234, media_ssrc=5678)
        packet.lost.append(7654)
        run(sender._handle_rtcp_packet(packet))

        # clean shutdown
        run(sender.stop())

    def test_handle_rtcp_pli(self):
        transport, remote = dummy_dtls_transport_pair()

        sender = RTCRtpSender(VideoStreamTrack(), transport)
        self.assertEqual(sender.kind, 'video')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP feedback NACK
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_PLI, ssrc=1234, media_ssrc=5678)
        run(sender._handle_rtcp_packet(packet))

        # clean shutdown
        run(sender.stop())

    def test_handle_rtcp_rr(self):
        transport, remote = dummy_dtls_transport_pair()

        sender = RTCRtpSender(VideoStreamTrack(), transport)
        self.assertEqual(sender.kind, 'video')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP RR
        for packet in RtcpPacket.parse(load('rtcp_rr.bin')):
            run(sender._handle_rtcp_packet(packet))

        # check stats
        report = run(sender.getStats())
        self.assertTrue(isinstance(report, RTCStatsReport))
        self.assertEqual(sorted(report.keys()), ['outbound-rtp', 'remote-inbound-rtp'])

        # clean shutdown
        run(sender.stop())

    def test_send_keyframe(self):
        """
        Ask for a keyframe.
        """
        transport = FakeDtlsTransport()

        sender = RTCRtpSender(VideoStreamTrack(), transport)
        self.assertEqual(sender.kind, 'video')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # wait for one packet to be transmitted, and ask for keyframe
        packet = run(transport.queue.get())
        sender._send_keyframe()

        # wait for packet to be transmitted
        rtx_packet = run(transport.queue.get())
        self.assertEqual(rtx_packet.sequence_number, seq_plus_one(packet.sequence_number))

        # clean shutdown
        run(sender.stop())

    def test_retransmit(self):
        """
        Ask for an RTP packet retransmission.
        """
        transport = FakeDtlsTransport()

        sender = RTCRtpSender(VideoStreamTrack(), transport)
        self.assertEqual(sender.kind, 'video')
        self.assertEqual(sender.transport, transport)

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # wait for one packet to be transmitted, and ask to retransmit
        packet = run(transport.queue.get())
        run(sender._retransmit(packet.sequence_number))

        # wait for packet to be transmitted
        rtx_packet = run(transport.queue.get())
        self.assertEqual(rtx_packet.sequence_number, packet.sequence_number)

        # clean shutdown
        run(sender.stop())

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
