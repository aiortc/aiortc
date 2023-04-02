import asyncio
from struct import pack
from unittest import TestCase
from unittest.mock import patch

from aiortc import MediaStreamTrack
from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack
from aiortc.rtcrtpparameters import (
    RTCRtpCapabilities,
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionCapability,
    RTCRtpParameters,
)
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.rtp import (
    RTCP_PSFB_APP,
    RTCP_PSFB_PLI,
    RTCP_RTPFB_NACK,
    RtcpPsfbPacket,
    RtcpReceiverInfo,
    RtcpRrPacket,
    RtcpRtpfbPacket,
    RtpPacket,
    is_rtcp,
    pack_remb_fci,
)
from aiortc.stats import RTCStatsReport

from tests.test_mediastreams import VideoPacketStreamTrack

from .utils import ClosedDtlsTransport, asynctest, dummy_dtls_transport_pair

VP8_CODEC = RTCRtpCodecParameters(
    mimeType="video/VP8", clockRate=90000, payloadType=100
)

H264_CODEC = RTCRtpCodecParameters(
    mimeType="video/H264", clockRate=90000, payloadType=98
)


class BuggyStreamTrack(MediaStreamTrack):
    kind = "audio"

    async def recv(self):
        raise Exception("I'm a buggy track!")


class RTCRtpSenderTest(TestCase):
    def test_capabilities(self):
        # audio
        capabilities = RTCRtpSender.getCapabilities("audio")
        self.assertIsInstance(capabilities, RTCRtpCapabilities)
        self.assertEqual(
            capabilities.codecs,
            [
                RTCRtpCodecCapability(
                    mimeType="audio/opus", clockRate=48000, channels=2
                ),
                RTCRtpCodecCapability(
                    mimeType="audio/PCMU", clockRate=8000, channels=1
                ),
                RTCRtpCodecCapability(
                    mimeType="audio/PCMA", clockRate=8000, channels=1
                ),
            ],
        )
        self.assertEqual(
            capabilities.headerExtensions,
            [
                RTCRtpHeaderExtensionCapability(
                    uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                ),
                RTCRtpHeaderExtensionCapability(
                    uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                ),
            ],
        )

        # video
        capabilities = RTCRtpSender.getCapabilities("video")
        self.assertIsInstance(capabilities, RTCRtpCapabilities)
        self.assertEqual(
            capabilities.codecs,
            [
                RTCRtpCodecCapability(mimeType="video/VP8", clockRate=90000),
                RTCRtpCodecCapability(mimeType="video/rtx", clockRate=90000),
                RTCRtpCodecCapability(
                    mimeType="video/H264",
                    clockRate=90000,
                    parameters={
                        "level-asymmetry-allowed": "1",
                        "packetization-mode": "1",
                        "profile-level-id": "42001f",
                    },
                ),
                RTCRtpCodecCapability(
                    mimeType="video/H264",
                    clockRate=90000,
                    parameters={
                        "level-asymmetry-allowed": "1",
                        "packetization-mode": "1",
                        "profile-level-id": "42e01f",
                    },
                ),
            ],
        )
        self.assertEqual(
            capabilities.headerExtensions,
            [
                RTCRtpHeaderExtensionCapability(
                    uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                ),
                RTCRtpHeaderExtensionCapability(
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"
                ),
            ],
        )

        # bogus
        with self.assertRaises(ValueError):
            RTCRtpSender.getCapabilities("bogus")

    @asynctest
    async def test_construct(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender("audio", local_transport)
            self.assertEqual(sender.kind, "audio")
            self.assertEqual(sender.transport, local_transport)

    def test_construct_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpSender("audio", dtlsTransport)

    @asynctest
    async def test_connection_error(self):
        """
        Close the underlying transport before the sender.
        """
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")

            await sender.send(RTCRtpParameters(codecs=[PCMU_CODEC]))

            await local_transport.stop()

    @asynctest
    async def test_handle_rtcp_nack(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # receive RTCP feedback NACK
            packet = RtcpRtpfbPacket(
                fmt=RTCP_RTPFB_NACK, ssrc=1234, media_ssrc=sender._ssrc
            )
            packet.lost.append(7654)
            await sender._handle_rtcp_packet(packet)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_handle_rtcp_pli(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # receive RTCP feedback NACK
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_PLI, ssrc=1234, media_ssrc=sender._ssrc
            )
            await sender._handle_rtcp_packet(packet)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_handle_rtcp_remb(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # receive RTCP feedback REMB
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(4160000, [sender._ssrc]),
            )
            await sender._handle_rtcp_packet(packet)

            # receive RTCP feedback REMB (malformed)
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP, ssrc=1234, media_ssrc=0, fci=b"JUNK"
            )
            await sender._handle_rtcp_packet(packet)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_handle_rtcp_rr(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # receive RTCP RR
            packet = RtcpRrPacket(
                ssrc=1234,
                reports=[
                    RtcpReceiverInfo(
                        ssrc=sender._ssrc,
                        fraction_lost=0,
                        packets_lost=0,
                        highest_sequence=630,
                        jitter=1906,
                        lsr=0,
                        dlsr=0,
                    )
                ],
            )
            await sender._handle_rtcp_packet(packet)

            # check stats
            report = await sender.getStats()
            self.assertIsInstance(report, RTCStatsReport)
            self.assertEqual(
                sorted([s.type for s in report.values()]),
                ["outbound-rtp", "remote-inbound-rtp", "transport"],
            )

            # clean shutdown
            await sender.stop()

    @patch("aiortc.rtcrtpsender.logger.isEnabledFor")
    @asynctest
    async def test_log_debug(self, mock_is_enabled_for):
        mock_is_enabled_for.return_value = True

        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_send_keyframe(self):
        """
        Ask for a keyframe.
        """
        queue = asyncio.Queue()

        async def mock_send_rtp(data):
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # wait for one packet to be transmitted, and ask for keyframe
            await queue.get()
            sender._send_keyframe()

            # wait for packet to be transmitted, then shutdown
            await asyncio.sleep(0.1)
            await sender.stop()

    @asynctest
    async def test_handle_encoded_packet(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoPacketStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[H264_CODEC]))

            # wait for cleanup
            await asyncio.sleep(0.1)
            await sender.stop()

    @asynctest
    async def test_retransmit(self):
        """
        Ask for an RTP packet retransmission.
        """
        queue = asyncio.Queue()

        async def mock_send_rtp(data):
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender._ssrc = 1234
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpParameters(codecs=[VP8_CODEC]))

            # wait for one packet to be transmitted, and ask to retransmit
            packet = await queue.get()
            await sender._retransmit(packet.sequence_number)

            # wait for packet to be retransmitted, then shutdown
            await asyncio.sleep(0.1)
            await sender.stop()

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

    @asynctest
    async def test_retransmit_with_rtx(self):
        """
        Ask for an RTP packet retransmission.
        """
        queue = asyncio.Queue()

        async def mock_send_rtp(data):
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender._ssrc = 1234
            sender._rtx_ssrc = 2345
            self.assertEqual(sender.kind, "video")

            await sender.send(
                RTCRtpParameters(
                    codecs=[
                        VP8_CODEC,
                        RTCRtpCodecParameters(
                            mimeType="video/rtx",
                            clockRate=90000,
                            payloadType=101,
                            parameters={"apt": 100},
                        ),
                    ]
                )
            )

            # wait for one packet to be transmitted, and ask to retransmit
            packet = await queue.get()
            await sender._retransmit(packet.sequence_number)

            # wait for packet to be retransmitted, then shutdown
            await asyncio.sleep(0.1)
            await sender.stop()

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
            self.assertEqual(found_rtx.payload[0:2], pack("!H", packet.sequence_number))

    @asynctest
    async def test_stop(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")

            await sender.send(RTCRtpParameters(codecs=[PCMU_CODEC]))

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_stop_before_send(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            await sender.stop()

    @asynctest
    async def test_stop_on_exception(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(BuggyStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")

            await sender.send(RTCRtpParameters(codecs=[PCMU_CODEC]))

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_track_ended(self):
        async with dummy_dtls_transport_pair() as (local_transport, _):
            track = AudioStreamTrack()
            sender = RTCRtpSender(track, local_transport)

            await sender.send(RTCRtpParameters(codecs=[PCMU_CODEC]))

            # stop track and wait for RTP loop to exit
            track.stop()
            await asyncio.sleep(0.1)
