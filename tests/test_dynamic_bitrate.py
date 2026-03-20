import asyncio
from unittest import TestCase

from av import VideoFrame

from aiortc.codecs.h264 import (
    DEFAULT_BITRATE as H264_DEFAULT_BITRATE,
    MAX_BITRATE as H264_MAX_BITRATE,
    MIN_BITRATE as H264_MIN_BITRATE,
    H264Encoder,
)
from aiortc.codecs.vpx import (
    DEFAULT_BITRATE as VP8_DEFAULT_BITRATE,
    MAX_BITRATE as VP8_MAX_BITRATE,
    MIN_BITRATE as VP8_MIN_BITRATE,
    Vp8Encoder,
)
from aiortc.mediastreams import QueuedVideoStreamTrack, VideoStreamTrack
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpSendParameters
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.rtp import RTCP_PSFB_APP, RtcpPsfbPacket, pack_remb_fci

from .codecs import CodecTestCase
from .utils import asynctest, dummy_dtls_transport_pair

VP8_CODEC = RTCRtpCodecParameters(
    mimeType="video/VP8", clockRate=90000, payloadType=100
)
H264_CODEC = RTCRtpCodecParameters(
    mimeType="video/H264", clockRate=90000, payloadType=98
)
OPUS_CODEC = RTCRtpCodecParameters(
    mimeType="audio/opus", clockRate=48000, payloadType=111, channels=2
)


class H264EncoderBitrateTest(CodecTestCase):
    def test_defaults(self) -> None:
        encoder = H264Encoder()
        self.assertEqual(encoder.target_bitrate, H264_DEFAULT_BITRATE)
        self.assertEqual(encoder._min_bitrate, H264_MIN_BITRATE)
        self.assertEqual(encoder._max_bitrate, H264_MAX_BITRATE)

    def test_set_within_range(self) -> None:
        encoder = H264Encoder()
        encoder.target_bitrate = 2000000
        self.assertEqual(encoder.target_bitrate, 2000000)

    def test_clamp_below_min(self) -> None:
        encoder = H264Encoder()
        encoder.target_bitrate = 1000
        self.assertEqual(encoder.target_bitrate, H264_MIN_BITRATE)

    def test_clamp_above_max(self) -> None:
        encoder = H264Encoder()
        encoder.target_bitrate = 100_000_000
        self.assertEqual(encoder.target_bitrate, H264_MAX_BITRATE)

    def test_custom_min_clamps_correctly(self) -> None:
        encoder = H264Encoder()
        encoder._min_bitrate = 200_000
        encoder.target_bitrate = 100_000
        self.assertEqual(encoder.target_bitrate, 200_000)

    def test_custom_max_clamps_correctly(self) -> None:
        encoder = H264Encoder()
        encoder._max_bitrate = 800_000
        encoder.target_bitrate = 2_000_000
        self.assertEqual(encoder.target_bitrate, 800_000)

    def test_custom_min_max_range(self) -> None:
        encoder = H264Encoder()
        encoder._min_bitrate = 200_000
        encoder._max_bitrate = 800_000
        encoder.target_bitrate = 500_000
        self.assertEqual(encoder.target_bitrate, 500_000)

    def test_encode_at_min_bitrate(self) -> None:
        encoder = H264Encoder()
        encoder.target_bitrate = H264_MIN_BITRATE
        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(payloads), 1)
        self.assertEqual(timestamp, 0)

    def test_encode_after_bitrate_change(self) -> None:
        encoder = H264Encoder()
        frame1 = self.create_video_frame(width=640, height=480, pts=0)
        encoder.encode(frame1)

        # > 10% change forces codec recreation on next encode
        encoder.target_bitrate = H264_MAX_BITRATE
        frame2 = self.create_video_frame(width=640, height=480, pts=3000)
        payloads, timestamp = encoder.encode(frame2)
        self.assertGreaterEqual(len(payloads), 1)
        self.assertEqual(encoder.target_bitrate, H264_MAX_BITRATE)


class Vp8EncoderBitrateTest(CodecTestCase):
    def test_defaults(self) -> None:
        encoder = Vp8Encoder()
        self.assertEqual(encoder.target_bitrate, VP8_DEFAULT_BITRATE)
        self.assertEqual(encoder._min_bitrate, VP8_MIN_BITRATE)
        self.assertEqual(encoder._max_bitrate, VP8_MAX_BITRATE)

    def test_set_within_range(self) -> None:
        encoder = Vp8Encoder()
        encoder.target_bitrate = 1_000_000
        self.assertEqual(encoder.target_bitrate, 1_000_000)

    def test_clamp_below_min(self) -> None:
        encoder = Vp8Encoder()
        encoder.target_bitrate = 1000
        self.assertEqual(encoder.target_bitrate, VP8_MIN_BITRATE)

    def test_clamp_above_max(self) -> None:
        encoder = Vp8Encoder()
        encoder.target_bitrate = 100_000_000
        self.assertEqual(encoder.target_bitrate, VP8_MAX_BITRATE)

    def test_custom_min_clamps_correctly(self) -> None:
        encoder = Vp8Encoder()
        encoder._min_bitrate = 300_000
        encoder.target_bitrate = 100_000
        self.assertEqual(encoder.target_bitrate, 300_000)

    def test_custom_max_clamps_correctly(self) -> None:
        encoder = Vp8Encoder()
        encoder._max_bitrate = 1_000_000
        encoder.target_bitrate = 2_000_000
        self.assertEqual(encoder.target_bitrate, 1_000_000)

    def test_custom_min_max_range(self) -> None:
        encoder = Vp8Encoder()
        encoder._min_bitrate = 300_000
        encoder._max_bitrate = 1_000_000
        encoder.target_bitrate = 600_000
        self.assertEqual(encoder.target_bitrate, 600_000)

    def test_encode_at_min_bitrate(self) -> None:
        encoder = Vp8Encoder()
        encoder.target_bitrate = VP8_MIN_BITRATE
        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertGreaterEqual(len(payloads), 1)
        self.assertEqual(timestamp, 0)

    def test_encode_after_bitrate_change(self) -> None:
        encoder = Vp8Encoder()
        frame1 = self.create_video_frame(width=640, height=480, pts=0)
        encoder.encode(frame1)

        encoder.target_bitrate = VP8_MAX_BITRATE
        frame2 = self.create_video_frame(width=640, height=480, pts=3000)
        payloads, timestamp = encoder.encode(frame2)
        self.assertGreaterEqual(len(payloads), 1)
        self.assertEqual(encoder.target_bitrate, VP8_MAX_BITRATE)


class RTCRtpSenderBitrateTest(TestCase):
    @asynctest
    async def test_target_bitrate_none_by_default(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            self.assertIsNone(sender.target_bitrate)
            await sender.stop()

    @asynctest
    async def test_on_bitrate_estimate_none_by_default(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender("video", local_transport)
            self.assertIsNone(sender.on_bitrate_estimate)
            await sender.stop()

    @asynctest
    async def test_on_bitrate_estimate_property_roundtrip(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender("video", local_transport)
            callback = lambda bps: None
            sender.on_bitrate_estimate = callback
            self.assertIs(sender.on_bitrate_estimate, callback)
            sender.on_bitrate_estimate = None
            self.assertIsNone(sender.on_bitrate_estimate)
            await sender.stop()

    @asynctest
    async def test_configure_bitrate_readable_before_send(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.configure_bitrate(800_000, 300_000, 2_000_000)
            self.assertEqual(sender.target_bitrate, 800_000)
            await sender.stop()

    @asynctest
    async def test_target_bitrate_setter_updates_config_before_encoder(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.configure_bitrate(800_000, 300_000, 2_000_000)
            sender.target_bitrate = 600_000
            self.assertEqual(sender.target_bitrate, 600_000)
            await sender.stop()

    @asynctest
    async def test_configure_bitrate_applied_when_encoder_is_created(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.configure_bitrate(800_000, 300_000, 2_000_000)

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)  # let encoder be created on first frame

            self.assertEqual(sender.target_bitrate, 800_000)
            await sender.stop()

    @asynctest
    async def test_configure_bitrate_on_running_encoder(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)  # let encoder be created

            sender.configure_bitrate(600_000, 200_000, 1_400_000)
            self.assertEqual(sender.target_bitrate, 600_000)
            await sender.stop()

    @asynctest
    async def test_target_bitrate_clamped_by_running_encoder(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.configure_bitrate(VP8_DEFAULT_BITRATE, VP8_MIN_BITRATE, VP8_MAX_BITRATE)

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)

            sender.target_bitrate = 1000  # below VP8_MIN_BITRATE
            self.assertEqual(sender.target_bitrate, VP8_MIN_BITRATE)

            sender.target_bitrate = 100_000_000  # above VP8_MAX_BITRATE
            self.assertEqual(sender.target_bitrate, VP8_MAX_BITRATE)
            await sender.stop()

    @asynctest
    async def test_remb_updates_target_bitrate(self) -> None:
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)

            remb_bitrate = 1_000_000
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(remb_bitrate, [sender._ssrc]),
            )
            await sender._handle_rtcp_packet(packet)

            self.assertEqual(sender.target_bitrate, remb_bitrate)
            await sender.stop()

    @asynctest
    async def test_remb_fires_on_bitrate_estimate_callback(self) -> None:
        received: list[int] = []

        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.on_bitrate_estimate = lambda bps: received.append(bps)

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)

            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(1_000_000, [sender._ssrc]),
            )
            await sender._handle_rtcp_packet(packet)

            self.assertEqual(len(received), 1)
            self.assertEqual(received[0], 1_000_000)
            await sender.stop()

    @asynctest
    async def test_remb_callback_receives_clamped_bitrate(self) -> None:
        """Callback value is the post-clamp bitrate stored in the encoder."""
        received: list[int] = []

        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.on_bitrate_estimate = lambda bps: received.append(bps)

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)

            # Send a REMB that exceeds VP8_MAX_BITRATE
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(100_000_000, [sender._ssrc]),
            )
            await sender._handle_rtcp_packet(packet)

            self.assertEqual(len(received), 1)
            self.assertEqual(received[0], VP8_MAX_BITRATE)
            await sender.stop()

    @asynctest
    async def test_remb_wrong_ssrc_does_not_fire_callback(self) -> None:
        received: list[int] = []

        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.on_bitrate_estimate = lambda bps: received.append(bps)

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)

            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(1_000_000, [sender._ssrc + 1]),  # wrong SSRC
            )
            await sender._handle_rtcp_packet(packet)

            self.assertEqual(len(received), 0)
            await sender.stop()

    @asynctest
    async def test_remb_without_encoder_does_not_crash(self) -> None:
        """REMB before any encoding should be silently ignored."""
        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender("video", local_transport)
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_APP,
                ssrc=1234,
                media_ssrc=0,
                fci=pack_remb_fci(1_000_000, [sender._ssrc]),
            )
            await sender._handle_rtcp_packet(packet)
            await sender.stop()

    @asynctest
    async def test_multiple_remb_packets_update_bitrate_each_time(self) -> None:
        received: list[int] = []

        async with dummy_dtls_transport_pair() as (local_transport, _):
            sender = RTCRtpSender(VideoStreamTrack(), local_transport)
            sender.on_bitrate_estimate = lambda bps: received.append(bps)

            await sender.send(RTCRtpSendParameters(codecs=[VP8_CODEC]))
            await asyncio.sleep(0.1)

            for bitrate in [800_000, 600_000, 1_200_000]:
                packet = RtcpPsfbPacket(
                    fmt=RTCP_PSFB_APP,
                    ssrc=1234,
                    media_ssrc=0,
                    fci=pack_remb_fci(bitrate, [sender._ssrc]),
                )
                await sender._handle_rtcp_packet(packet)

            self.assertEqual(received, [800_000, 600_000, 1_200_000])
            self.assertEqual(sender.target_bitrate, 1_200_000)
            await sender.stop()


class QueuedVideoStreamTrackTest(CodecTestCase):
    def test_kind(self) -> None:
        track = QueuedVideoStreamTrack()
        self.assertEqual(track.kind, "video")

    def test_id_is_uuid(self) -> None:
        track = QueuedVideoStreamTrack()
        self.assertEqual(len(track.id), 36)

    def test_ready_state_is_live(self) -> None:
        track = QueuedVideoStreamTrack()
        self.assertEqual(track.readyState, "live")

    def test_stop_sets_ready_state_ended(self) -> None:
        track = QueuedVideoStreamTrack()
        track.stop()
        self.assertEqual(track.readyState, "ended")

    def test_default_buffer_size(self) -> None:
        track = QueuedVideoStreamTrack()
        self.assertEqual(track.buffer_size, 30)

    def test_custom_buffer_size(self) -> None:
        track = QueuedVideoStreamTrack(buffer_size=10)
        self.assertEqual(track.buffer_size, 10)

    def test_initial_queue_size_is_zero(self) -> None:
        track = QueuedVideoStreamTrack()
        self.assertEqual(track.queue_size, 0)

    @asynctest
    async def test_put_increments_queue_size(self) -> None:
        track = QueuedVideoStreamTrack()
        await track.put(self.create_video_frame(width=640, height=480, pts=0))
        self.assertEqual(track.queue_size, 1)
        await track.put(self.create_video_frame(width=640, height=480, pts=1))
        self.assertEqual(track.queue_size, 2)

    @asynctest
    async def test_recv_returns_put_frame(self) -> None:
        track = QueuedVideoStreamTrack()
        frame = self.create_video_frame(width=640, height=480, pts=42)
        await track.put(frame)
        received = await track.recv()
        self.assertIs(received, frame)

    @asynctest
    async def test_recv_decrements_queue_size(self) -> None:
        track = QueuedVideoStreamTrack()
        await track.put(self.create_video_frame(width=640, height=480, pts=0))
        await track.put(self.create_video_frame(width=640, height=480, pts=1))
        await track.recv()
        self.assertEqual(track.queue_size, 1)
        await track.recv()
        self.assertEqual(track.queue_size, 0)

    @asynctest
    async def test_fifo_order_preserved(self) -> None:
        track = QueuedVideoStreamTrack()
        frames = [self.create_video_frame(width=640, height=480, pts=i) for i in range(5)]
        for frame in frames:
            await track.put(frame)
        for expected in frames:
            self.assertIs(await track.recv(), expected)

    @asynctest
    async def test_put_when_full_drops_oldest_frame(self) -> None:
        track = QueuedVideoStreamTrack(buffer_size=2)
        frame_a = self.create_video_frame(width=640, height=480, pts=0)
        frame_b = self.create_video_frame(width=640, height=480, pts=1)
        frame_c = self.create_video_frame(width=640, height=480, pts=2)

        await track.put(frame_a)
        await track.put(frame_b)
        # queue is full: [A, B]
        await track.put(frame_c)
        # A is dropped: [B, C]

        self.assertEqual(track.queue_size, 2)
        self.assertIs(await track.recv(), frame_b)
        self.assertIs(await track.recv(), frame_c)

    @asynctest
    async def test_put_when_full_queue_size_stays_at_max(self) -> None:
        track = QueuedVideoStreamTrack(buffer_size=3)
        for i in range(3):
            await track.put(self.create_video_frame(width=640, height=480, pts=i))
        await track.put(self.create_video_frame(width=640, height=480, pts=99))  # overflow
        self.assertEqual(track.queue_size, 3)

    @asynctest
    async def test_buffer_size_unchanged_after_overflow(self) -> None:
        track = QueuedVideoStreamTrack(buffer_size=5)
        for i in range(10):
            await track.put(self.create_video_frame(width=640, height=480, pts=i))
        self.assertEqual(track.buffer_size, 5)

    @asynctest
    async def test_repeated_overflow_only_keeps_latest_frames(self) -> None:
        track = QueuedVideoStreamTrack(buffer_size=2)
        frames = [self.create_video_frame(width=640, height=480, pts=i) for i in range(6)]
        for frame in frames:
            await track.put(frame)
        # Only the last 2 should survive
        self.assertIs(await track.recv(), frames[4])
        self.assertIs(await track.recv(), frames[5])

    @asynctest
    async def test_recv_blocks_until_frame_is_available(self) -> None:
        track = QueuedVideoStreamTrack()
        frame = self.create_video_frame(width=640, height=480, pts=7)

        async def put_after_delay() -> None:
            await asyncio.sleep(0.05)
            await track.put(frame)

        task = asyncio.create_task(put_after_delay())
        received = await track.recv()
        await task

        self.assertIs(received, frame)

    @asynctest
    async def test_interleaved_put_recv(self) -> None:
        track = QueuedVideoStreamTrack(buffer_size=5)
        for i in range(3):
            frame = self.create_video_frame(width=640, height=480, pts=i)
            await track.put(frame)
            received = await track.recv()
            self.assertEqual(received.pts, i)
        self.assertEqual(track.queue_size, 0)
