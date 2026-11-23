import asyncio
import fractions
import itertools
from struct import pack
from unittest import TestCase
from unittest.mock import MagicMock, patch

from aiortc import MediaStreamTrack
from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioStreamTrack, VideoStreamTrack
from aiortc.rtcrtpparameters import (
    RTCRtpCapabilities,
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionCapability,
    RTCRtpSendParameters,
)
from aiortc.rtcrtpsender import RTCEncodedFrame, RTCRtpSender, RtpTimeline
from aiortc.rtp import (
    RTCP_PSFB_APP,
    RTCP_PSFB_FIR,
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
from aiortc.utils import uint32_add
from av import AudioFrame

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

    async def recv(self) -> None:
        raise Exception("I'm a buggy track!")


class StereoAudioStreamTrack(MediaStreamTrack):
    """
    A dummy audio track which reads silence in stereo at 44.1kHz.
    """

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._timestamp = 0

    async def recv(self) -> AudioFrame:
        await asyncio.sleep(0.02)
        frame = AudioFrame(format="s16", layout="stereo", samples=882)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = self._timestamp
        frame.sample_rate = 44100
        frame.time_base = fractions.Fraction(1, 44100)
        self._timestamp += 882
        return frame


def timestamp_steps(timestamps: list[int]) -> list[int]:
    """
    Return the steps between consecutive RTP timestamps.
    """
    return [uint32_add(b, -a) for a, b in zip(timestamps, timestamps[1:])]


class RTCRtpSenderTest(TestCase):
    def test_capabilities(self) -> None:
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
                    mimeType="audio/G722", clockRate=8000, channels=1
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
    async def test_construct(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender("audio", local_transport)
            self.assertEqual(sender.kind, "audio")
            self.assertEqual(sender.transport, local_transport)

    def test_construct_invalid_dtls_transport_state(self) -> None:
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpSender("audio", dtlsTransport)  # type: ignore

    @asynctest
    async def test_connection_error(self) -> None:
        """
        Close the underlying transport before the sender.
        """
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")

            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))

            await local_transport.stop()

    @asynctest
    async def test_handle_rtcp_nack(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

            # receive RTCP feedback NACK
            packet = RtcpRtpfbPacket(
                fmt=RTCP_RTPFB_NACK, ssrc=1234, media_ssrc=sender._ssrc
            )
            packet.lost.append(7654)
            await sender._handle_rtcp_packet(packet)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_handle_rtcp_pli(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

            # receive RTCP feedback PLI
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_PLI, ssrc=1234, media_ssrc=sender._ssrc
            )
            await sender._handle_rtcp_packet(packet)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_handle_rtcp_fir(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

            # receive RTCP feedback FIR
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_FIR, ssrc=1234, media_ssrc=sender._ssrc
            )
            await sender._handle_rtcp_packet(packet)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_handle_rtcp_remb(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

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
    async def test_handle_rtcp_rr(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

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
    async def test_log_debug(self, mock_is_enabled_for: MagicMock) -> None:
        mock_is_enabled_for.return_value = True

        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_send_keyframe(self) -> None:
        """
        Ask for a keyframe.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

            # wait for one packet to be transmitted, and ask for keyframe
            await queue.get()
            sender._send_keyframe()

            # wait for packet to be transmitted, then shutdown
            await asyncio.sleep(0.1)
            await sender.stop()

    @asynctest
    async def test_handle_encoded_packet(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoPacketStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[H264_CODEC]))

            # wait for cleanup
            await asyncio.sleep(0.1)
            await sender.stop()

    @asynctest
    async def test_retransmit(self) -> None:
        """
        Ask for an RTP packet retransmission.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender._ssrc = 1234
            self.assertEqual(sender.kind, "video")

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

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
    async def test_retransmit_with_rtx(self) -> None:
        """
        Ask for an RTP packet retransmission.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender._ssrc = 1234
            sender._rtx_ssrc = 2345
            self.assertEqual(sender.kind, "video")

            await sender.send(
                RTCRtpSendParameters(
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
    async def test_marker_audio(self) -> None:
        """
        The marker bit is only set when sending starts or resumes.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))

            markers = [(await queue.get()).marker for _ in range(3)]
            self.assertEqual(markers, [1, 0, 0])

            # pause and resume sending
            sender._enabled = False
            await asyncio.sleep(0.1)
            while not queue.empty():
                queue.get_nowait()
            sender._enabled = True

            markers = [(await queue.get()).marker for _ in range(3)]
            self.assertEqual(markers, [1, 0, 0])

            # remove and restore the track
            sender.replaceTrack(None)
            await asyncio.sleep(0.1)
            while not queue.empty():
                queue.get_nowait()
            sender.replaceTrack(AudioStreamTrack())

            markers = [(await queue.get()).marker for _ in range(3)]
            self.assertEqual(markers, [1, 0, 0])

            await sender.stop()

    @asynctest
    async def test_marker_video(self) -> None:
        """
        The marker bit is set on the last packet of each frame.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        timestamps = itertools.count(step=3000)

        async def mock_next_encoded_frame(
            codec: RTCRtpCodecParameters, track: MediaStreamTrack
        ) -> RTCEncodedFrame:
            await asyncio.sleep(0.02)
            return RTCEncodedFrame([b"a", b"b", b"c"], next(timestamps), None)

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            # The track only sets the kind, frames of three packets come from
            # the mock.
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender._next_encoded_frame = mock_next_encoded_frame  # type: ignore
            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))

            markers = [(await queue.get()).marker for _ in range(6)]
            self.assertEqual(markers, [0, 0, 1, 0, 0, 1])

            await sender.stop()

    @asynctest
    async def test_replace_track_audio(self) -> None:
        """
        RTP timestamps carry on when the track is replaced by one in another
        format.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))
            timestamps = [(await queue.get()).timestamp for _ in range(3)]

            sender.replaceTrack(StereoAudioStreamTrack())
            for _ in range(5):
                packet = await asyncio.wait_for(queue.get(), 1)
                timestamps.append(packet.timestamp)

            # Each frame lasts 160 ticks, the switch may add the elapsed time.
            steps = timestamp_steps(timestamps)
            self.assertTrue(all(160 <= step < 8000 for step in steps), steps)

            await sender.stop()

    @asynctest
    async def test_replace_track_video(self) -> None:
        """
        RTP timestamps carry on when the track is replaced, even after a change
        of bitrate recreated the encoder.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await queue.get()

            # Receive RTCP feedback REMB, which changes the bitrate.
            remb = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(1000000, [sender._ssrc]),
            )
            await sender._handle_rtcp_packet(remb)
            timestamps = [(await queue.get()).timestamp for _ in range(3)]

            sender.replaceTrack(VideoStreamTrack())
            for _ in range(3):
                packet = await asyncio.wait_for(queue.get(), 1)
                timestamps.append(packet.timestamp)

            # Each frame lasts about 3000 ticks, the switch may add the elapsed
            # time.
            steps = timestamp_steps(timestamps)
            self.assertTrue(all(2990 <= step < 90000 for step in steps), steps)

            await sender.stop()

    @asynctest
    async def test_replace_track_wrong_kind(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            track = AudioStreamTrack()
            sender = RTCRtpSender(track, local_transport)

            with self.assertRaises(TypeError) as cm:
                sender.replaceTrack(VideoStreamTrack())
            self.assertEqual(str(cm.exception), "Expected a track of kind 'audio'")
            self.assertIs(sender.track, track)

    @asynctest
    async def test_disabled(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")
            self.assertEqual(sender._enabled, True)

            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))
            sender._enabled = False
            self.assertEqual(sender._enabled, False)

            # check stats
            report = await sender.getStats()
            self.assertIsInstance(report, RTCStatsReport)
            self.assertEqual(
                sorted([s.type for s in report.values()]),
                ["outbound-rtp", "transport"],
            )

            outbound_rtp = report["outbound-rtp_" + str(id(sender))]
            self.assertEqual(outbound_rtp.packetsSent, 0)

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_stop(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")

            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_stop_before_send(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(AudioStreamTrack(), local_transport)
            await sender.stop()

    @asynctest
    async def test_stop_on_exception(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(BuggyStreamTrack(), local_transport)
            self.assertEqual(sender.kind, "audio")

            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))

            # clean shutdown
            await sender.stop()

    @asynctest
    async def test_track_ended(self) -> None:
        """
        Once its track has ended, the sender sends nothing until the track is
        replaced.
        """
        queue: asyncio.Queue[RtpPacket] = asyncio.Queue()

        async def mock_send_rtp(data: bytes) -> None:
            if not is_rtcp(data):
                await queue.put(RtpPacket.parse(data))

        async with dummy_dtls_transport_pair() as (local_transport, _):
            local_transport._send_rtp = mock_send_rtp  # type: ignore

            track = AudioStreamTrack()
            sender = RTCRtpSender(track, local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[PCMU_CODEC]))
            await queue.get()

            # End the track, the sender keeps it but stops sending.
            track.stop()
            await asyncio.sleep(0.1)
            while not queue.empty():
                queue.get_nowait()
            await asyncio.sleep(0.1)
            self.assertTrue(queue.empty())
            self.assertIs(sender.track, track)

            # Replace the track, sending resumes with a new talkspurt.
            sender.replaceTrack(AudioStreamTrack())
            packet = await asyncio.wait_for(queue.get(), 1)
            self.assertEqual(packet.marker, 1)

            await sender.stop()


class RtpTimelineTest(TestCase):
    @patch("aiortc.rtcrtpsender.random32")
    @patch("aiortc.rtcrtpsender.time.monotonic")
    def test_restart(self, mock_monotonic: MagicMock, mock_random32: MagicMock) -> None:
        mock_random32.return_value = 1000
        timeline = RtpTimeline(clock_rate=8000)

        # Before any frame, a restart has no effect.
        timeline.restart()
        mock_monotonic.return_value = 0.0
        self.assertEqual(timeline.timestamp(0), 1000)
        mock_monotonic.return_value = 0.125
        self.assertEqual(timeline.timestamp(160), 1160)

        # A new source starting at once does not overlap the last frame.
        timeline.restart()
        self.assertEqual(timeline.timestamp(0), 1320)
        mock_monotonic.return_value = 0.25
        self.assertEqual(timeline.timestamp(160), 1480)

        # A new source starting after a gap carries on after the elapsed time.
        timeline.restart()
        mock_monotonic.return_value = 1.25
        self.assertEqual(timeline.timestamp(5000), 9480)
        mock_monotonic.return_value = 1.375
        self.assertEqual(timeline.timestamp(5160), 9640)
