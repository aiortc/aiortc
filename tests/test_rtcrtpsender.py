import asyncio
from struct import pack
from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpParameters
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.rtp import (RTCP_PSFB_APP, RTCP_PSFB_PLI, RTCP_RTPFB_NACK,
                        RtcpPacket, RtcpPsfbPacket, RtcpRtpfbPacket, RtpPacket,
                        is_rtcp)
from aiortc.stats import RTCStatsReport

from .utils import dummy_dtls_transport_pair, load, run


class RTCRtpSenderTest(TestCase):
    def setUp(self):
        self.local_transport, self.remote_transport = dummy_dtls_transport_pair()

    def tearDown(self):
        run(self.local_transport.stop())
        run(self.remote_transport.stop())

    def test_construct(self):
        sender = RTCRtpSender('audio', self.local_transport)
        self.assertEqual(sender.kind, 'audio')
        self.assertEqual(sender.transport, self.local_transport)

    def test_construct_invalid_dtls_transport_state(self):
        run(self.local_transport.stop())
        with self.assertRaises(InvalidStateError):
            RTCRtpSender('audio', self.local_transport)

    def test_connection_error(self):
        """
        Close the underlying transport before the sender.
        """
        sender = RTCRtpSender(AudioStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'audio')

        run(sender.send(RTCRtpParameters(codecs=[PCMU_CODEC])))

        run(self.local_transport.stop())

    def test_handle_rtcp_nack(self):
        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'video')

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
        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'video')

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP feedback NACK
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_PLI, ssrc=1234, media_ssrc=5678)
        run(sender._handle_rtcp_packet(packet))

        # clean shutdown
        run(sender.stop())

    def test_handle_rtcp_remb(self):
        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'video')

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP feedback REMB
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_APP, ssrc=1234, media_ssrc=0,
                                fci=b'REMB\x01\x13\xf7\xa0\x96\xbe\x96\xcf')
        run(sender._handle_rtcp_packet(packet))

        # receive RTCP feedback REMB (malformed)
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_APP, ssrc=1234, media_ssrc=0,
                                fci=b'JUNK')
        run(sender._handle_rtcp_packet(packet))

        # clean shutdown
        run(sender.stop())

    def test_handle_rtcp_rr(self):
        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'video')

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP RR
        for packet in RtcpPacket.parse(load('rtcp_rr.bin')):
            run(sender._handle_rtcp_packet(packet))

        # check stats
        report = run(sender.getStats())
        self.assertTrue(isinstance(report, RTCStatsReport))
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            ['outbound-rtp', 'remote-inbound-rtp', 'transport'])

        # clean shutdown
        run(sender.stop())

    def test_send_keyframe(self):
        """
        Ask for a keyframe.
        """
        queue = asyncio.Queue()

        async def mock_send_rtp(data):
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))
        self.local_transport._send_rtp = mock_send_rtp

        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'video')

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # wait for one packet to be transmitted, and ask for keyframe
        run(queue.get())
        sender._send_keyframe()

        # wait for packet to be transmitted, then shutdown
        run(asyncio.sleep(0.5))
        run(sender.stop())

    def test_retransmit(self):
        """
        Ask for an RTP packet retransmission.
        """
        queue = asyncio.Queue()

        async def mock_send_rtp(data):
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))
        self.local_transport._send_rtp = mock_send_rtp

        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        sender._ssrc = 1234
        self.assertEqual(sender.kind, 'video')

        run(sender.send(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # wait for one packet to be transmitted, and ask to retransmit
        packet = run(queue.get())
        run(sender._retransmit(packet.sequence_number))

        # wait for packet to be retransmitted, then shutdown
        run(asyncio.sleep(0.5))
        run(sender.stop())

        # check packet was retransmitted
        found_rtx = None
        while not queue.empty():
            queue_packet = queue.get_nowait()
            if queue_packet.sequence_number == packet.sequence_number:
                found_rtx = queue_packet
                break
        self.assertIsNotNone(found_rtx)
        self.assertEqual(found_rtx.payload_type, 100)
        self.assertEqual(found_rtx.ssrc, 1234)

    def test_retransmit_with_rtx(self):
        """
        Ask for an RTP packet retransmission.
        """
        queue = asyncio.Queue()

        async def mock_send_rtp(data):
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))
        self.local_transport._send_rtp = mock_send_rtp

        sender = RTCRtpSender(VideoStreamTrack(), self.local_transport)
        sender._ssrc = 1234
        sender._rtx_ssrc = 2345
        self.assertEqual(sender.kind, 'video')

        run(sender.send(RTCRtpParameters(
            codecs=[
                RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
                RTCRtpCodecParameters(name='rtx', clockRate=90000, payloadType=101,
                                      parameters={'apt': 100})
            ])))

        # wait for one packet to be transmitted, and ask to retransmit
        packet = run(queue.get())
        run(sender._retransmit(packet.sequence_number))

        # wait for packet to be retransmitted, then shutdown
        run(asyncio.sleep(0.5))
        run(sender.stop())

        # check packet was retransmitted
        found_rtx = None
        while not queue.empty():
            queue_packet = queue.get_nowait()
            if queue_packet.payload_type == 101:
                found_rtx = queue_packet
                break
        self.assertIsNotNone(found_rtx)
        self.assertEqual(found_rtx.payload_type, 101)
        self.assertEqual(found_rtx.ssrc, 2345)
        self.assertEqual(found_rtx.payload[0:2], pack('!H', packet.sequence_number))

    def test_stop(self):
        sender = RTCRtpSender(AudioStreamTrack(), self.local_transport)
        self.assertEqual(sender.kind, 'audio')

        run(sender.send(RTCRtpParameters(codecs=[PCMU_CODEC])))

        # clean shutdown
        run(sender.stop())

    def test_stop_before_send(self):
        sender = RTCRtpSender(AudioStreamTrack(), self.local_transport)
        run(sender.stop())

    def test_track_ended(self):
        track = AudioStreamTrack()
        sender = RTCRtpSender(track, self.local_transport)
        run(sender.send(RTCRtpParameters(codecs=[PCMU_CODEC])))

        track.stop()
        run(asyncio.sleep(0.5))
