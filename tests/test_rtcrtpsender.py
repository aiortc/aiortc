import asyncio
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


class ClosedDtlsTransport:
    state = 'closed'


class FakeDtlsTransport:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.state = 'connected'

    def _register_rtp_sender(self, sender, parameters):
        pass

    def _unregister_rtp_sender(self, sender):
        pass

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

    def test_handle_rtcp_remb(self):
        transport, remote = dummy_dtls_transport_pair()

        sender = RTCRtpSender(VideoStreamTrack(), transport)
        self.assertEqual(sender.kind, 'video')
        self.assertEqual(sender.transport, transport)

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
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            ['outbound-rtp', 'remote-inbound-rtp', 'transport'])

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
        run(transport.queue.get())
        sender._send_keyframe()

        # wait for packet to be transmitted, then shutdown
        run(asyncio.sleep(0.5))
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

        # wait for packet to be retransmitted, then shutdown
        run(asyncio.sleep(0.5))
        run(sender.stop())

        # check packet was retransmitted
        found_rtx = False
        while not transport.queue.empty():
            queue_packet = transport.queue.get_nowait()
            if queue_packet.sequence_number == packet.sequence_number:
                found_rtx = True
        self.assertTrue(found_rtx)

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

    def test_track_ended(self):
        transport, _ = dummy_dtls_transport_pair()

        track = AudioStreamTrack()
        sender = RTCRtpSender(track, transport)
        run(sender.send(RTCRtpParameters(codecs=[PCMU_CODEC])))

        track.stop()
        run(asyncio.sleep(0.5))

        run(transport.stop())
