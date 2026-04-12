"""End-to-end WebRTC recovery test using local loopback RTCPeerConnection pairs.

Tests the full recovery pipeline:
1. Sender encodes video frames (320x240 colored rectangles) via H264
2. Receiver receives decoded frames through the aiortc internal pipeline
3. After some frames arrive, simulate packet loss by monkey-patching the
   DTLS transport to drop RTP packets for a brief window
4. Verify that after the disruption period ends, frames resume being received
   (recovery via PLI -> keyframe)

Run: python -m pytest tests/test_recovery_e2e.py -v -s
"""

import asyncio
import logging
from unittest import TestCase

from av import VideoFrame

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack, VideoStreamTrack

from .utils import asynctest

logger = logging.getLogger(__name__)

# How long to wait for ICE / connection states to settle (seconds).
ICE_SETTLE_TIMEOUT = 5.0
# Maximum total test duration.
TEST_TIMEOUT = 30.0
# Number of frames to collect before triggering packet loss.
FRAMES_BEFORE_LOSS = 3
# Duration of the simulated packet-loss window (seconds).
LOSS_DURATION = 1.0
# Number of frames to collect after recovery to confirm success.
FRAMES_AFTER_RECOVERY = 3


# ---------------------------------------------------------------------------
# Custom video source track that emits colored rectangles
# ---------------------------------------------------------------------------

class ColorPatternTrack(VideoStreamTrack):
    """
    Generates 320x240 video frames at 30 fps.

    Each frame is filled with a solid color that cycles through a palette,
    making it easy to visually distinguish frames if debugging.
    """

    kind = "video"

    COLORS_YUV = [
        # (Y, U, V) approximate values for recognizable colors
        (81, 90, 240),    # red-ish
        (145, 54, 34),    # green-ish
        (41, 240, 110),   # blue-ish
        (210, 16, 146),   # yellow-ish
    ]

    def __init__(self) -> None:
        super().__init__()
        self._frame_index = 0

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()

        width, height = 320, 240
        y_val, u_val, v_val = self.COLORS_YUV[self._frame_index % len(self.COLORS_YUV)]
        self._frame_index += 1

        frame = VideoFrame(width=width, height=height, format="yuv420p")
        # Fill Y plane
        y_plane = frame.planes[0]
        y_bytes = bytes([y_val] * (y_plane.line_size * height))
        y_plane.update(y_bytes)
        # Fill U plane (half resolution)
        u_plane = frame.planes[1]
        u_bytes = bytes([u_val] * (u_plane.line_size * (height // 2)))
        u_plane.update(u_bytes)
        # Fill V plane (half resolution)
        v_plane = frame.planes[2]
        v_bytes = bytes([v_val] * (v_plane.line_size * (height // 2)))
        v_plane.update(v_bytes)

        frame.pts = pts
        frame.time_base = time_base
        return frame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def wait_for(condition, timeout: float = 5.0, interval: float = 0.05):
    """Poll *condition()* until truthy or *timeout* expires."""
    elapsed = 0.0
    while elapsed < timeout:
        if condition():
            return
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Condition not met within {timeout}s")


async def negotiate(
    pc_sender: RTCPeerConnection,
    pc_receiver: RTCPeerConnection,
) -> None:
    """Run the full offer/answer exchange between two peer connections."""
    offer = await pc_sender.createOffer()
    await pc_sender.setLocalDescription(offer)
    await pc_receiver.setRemoteDescription(pc_sender.localDescription)

    answer = await pc_receiver.createAnswer()
    await pc_receiver.setLocalDescription(answer)
    await pc_sender.setRemoteDescription(pc_receiver.localDescription)


async def wait_ice_completed(
    pc1: RTCPeerConnection,
    pc2: RTCPeerConnection,
    timeout: float = ICE_SETTLE_TIMEOUT,
) -> None:
    """Wait until both peer connections reach ICE 'completed' state."""
    await wait_for(
        lambda: (
            pc1.iceConnectionState == "completed"
            and pc2.iceConnectionState == "completed"
        ),
        timeout=timeout,
    )


def get_dtls_transport(pc: RTCPeerConnection):
    """Return the (first) DTLS transport used by a peer connection."""
    transceivers = pc.getTransceivers()
    assert transceivers, "No transceivers on peer connection"
    return transceivers[0].sender.transport


# ---------------------------------------------------------------------------
# Packet-loss simulation via monkey-patching
# ---------------------------------------------------------------------------

class PacketDropper:
    """
    Wraps a DTLS transport's ``_send_rtp`` method so that during the
    ``dropping`` window all outgoing RTP/RTCP bytes are silently discarded.

    Usage::

        dropper = PacketDropper(dtls_transport)
        dropper.install()       # start intercepting
        dropper.dropping = True # start dropping
        ...
        dropper.dropping = False  # stop dropping; packets flow again
        dropper.uninstall()     # restore original method
    """

    def __init__(self, dtls_transport) -> None:
        self._transport = dtls_transport
        self._original_send_rtp = dtls_transport._send_rtp
        self.dropping = False
        self.dropped_count = 0

    def install(self) -> None:
        outer = self

        async def patched_send_rtp(data: bytes) -> None:
            if outer.dropping:
                outer.dropped_count += 1
                return  # silently discard
            return await outer._original_send_rtp(data)

        self._transport._send_rtp = patched_send_rtp

    def uninstall(self) -> None:
        self._transport._send_rtp = self._original_send_rtp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class RecoveryEndToEndTest(TestCase):
    """
    End-to-end test: two RTCPeerConnection instances connected via local
    loopback.  The sender streams H264-encoded colored rectangles.  After the
    receiver has collected a few frames we inject a packet-loss window by
    monkey-patching the sender's DTLS transport.  We then verify that
    decoded frames resume on the receiver side (recovery via PLI / keyframe).
    """

    @asynctest
    async def test_recovery_after_packet_loss(self) -> None:
        """
        Full pipeline: sender -> encode -> RTP -> (drop window) -> decode -> receiver track.
        After dropping stops, the receiver must eventually get new frames.
        """
        pc_sender = RTCPeerConnection()
        pc_receiver = RTCPeerConnection()

        received_frames: list[VideoFrame] = []
        receiver_track: list[MediaStreamTrack] = []

        @pc_receiver.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            receiver_track.append(track)

        try:
            # --- Add video track (sender -> receiver) ---
            sender_track = ColorPatternTrack()
            pc_sender.addTrack(sender_track)

            # --- Offer / Answer ---
            await negotiate(pc_sender, pc_receiver)

            # --- Wait for ICE to complete ---
            await wait_ice_completed(pc_sender, pc_receiver, timeout=ICE_SETTLE_TIMEOUT)

            # --- Wait for the remote track to appear ---
            await wait_for(lambda: len(receiver_track) > 0, timeout=5.0)
            remote_track = receiver_track[0]

            # --- Phase 1: Collect frames before loss ---
            for _ in range(FRAMES_BEFORE_LOSS):
                frame = await asyncio.wait_for(remote_track.recv(), timeout=10.0)
                received_frames.append(frame)

            pre_loss_count = len(received_frames)
            assert pre_loss_count >= FRAMES_BEFORE_LOSS, (
                f"Expected at least {FRAMES_BEFORE_LOSS} frames before loss, "
                f"got {pre_loss_count}"
            )

            # --- Phase 2: Simulate packet loss ---
            dtls = get_dtls_transport(pc_sender)
            dropper = PacketDropper(dtls)
            dropper.install()
            dropper.dropping = True

            # Let the loss window run for a bit.  During this time the
            # receiver should stop getting new frames.
            await asyncio.sleep(LOSS_DURATION)

            dropped = dropper.dropped_count
            assert dropped > 0, "Dropper should have intercepted some packets"

            # --- Phase 3: Stop dropping -> recovery ---
            dropper.dropping = False

            # The receiver's jitter buffer will detect a gap and send a PLI.
            # The sender, upon receiving the PLI, forces a keyframe.
            # After that the decoder should produce frames again.
            post_loss_frames: list[VideoFrame] = []
            for _ in range(FRAMES_AFTER_RECOVERY):
                # Give generous timeout for recovery (PLI round-trip + encode).
                frame = await asyncio.wait_for(remote_track.recv(), timeout=15.0)
                post_loss_frames.append(frame)

            assert len(post_loss_frames) >= FRAMES_AFTER_RECOVERY, (
                f"Expected at least {FRAMES_AFTER_RECOVERY} frames after recovery, "
                f"got {len(post_loss_frames)}"
            )

            dropper.uninstall()

        finally:
            await pc_sender.close()
            await pc_receiver.close()

    @asynctest
    async def test_recovery_multiple_loss_bursts(self) -> None:
        """
        Verify recovery works across multiple successive loss bursts.
        Each burst should trigger a PLI and the stream should resume.
        """
        pc_sender = RTCPeerConnection()
        pc_receiver = RTCPeerConnection()

        receiver_track: list[MediaStreamTrack] = []

        @pc_receiver.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            receiver_track.append(track)

        try:
            sender_track = ColorPatternTrack()
            pc_sender.addTrack(sender_track)

            await negotiate(pc_sender, pc_receiver)
            await wait_ice_completed(pc_sender, pc_receiver, timeout=ICE_SETTLE_TIMEOUT)
            await wait_for(lambda: len(receiver_track) > 0, timeout=5.0)

            remote_track = receiver_track[0]

            dtls = get_dtls_transport(pc_sender)
            dropper = PacketDropper(dtls)
            dropper.install()

            num_bursts = 3
            for burst_idx in range(num_bursts):
                # Collect a couple of frames to prove the stream is alive.
                for _ in range(2):
                    await asyncio.wait_for(remote_track.recv(), timeout=10.0)

                # Drop packets for a short burst.
                dropper.dropping = True
                dropper.dropped_count = 0
                await asyncio.sleep(0.5)
                assert dropper.dropped_count > 0, (
                    f"Burst {burst_idx}: no packets were dropped"
                )
                dropper.dropping = False

                # After each burst, frames must resume.
                frame = await asyncio.wait_for(remote_track.recv(), timeout=15.0)
                assert frame is not None, (
                    f"Burst {burst_idx}: no frame received after recovery"
                )

            dropper.uninstall()

        finally:
            await pc_sender.close()
            await pc_receiver.close()

    @asynctest
    async def test_frames_are_valid_after_recovery(self) -> None:
        """
        After recovery, the decoded frames should have valid dimensions
        and a plausible pixel format (not garbage / zero-size).
        """
        pc_sender = RTCPeerConnection()
        pc_receiver = RTCPeerConnection()

        receiver_track: list[MediaStreamTrack] = []

        @pc_receiver.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            receiver_track.append(track)

        try:
            sender_track = ColorPatternTrack()
            pc_sender.addTrack(sender_track)

            await negotiate(pc_sender, pc_receiver)
            await wait_ice_completed(pc_sender, pc_receiver, timeout=ICE_SETTLE_TIMEOUT)
            await wait_for(lambda: len(receiver_track) > 0, timeout=5.0)

            remote_track = receiver_track[0]

            # Collect a few baseline frames.
            for _ in range(FRAMES_BEFORE_LOSS):
                await asyncio.wait_for(remote_track.recv(), timeout=10.0)

            # Drop packets.
            dtls = get_dtls_transport(pc_sender)
            dropper = PacketDropper(dtls)
            dropper.install()
            dropper.dropping = True
            await asyncio.sleep(LOSS_DURATION)
            dropper.dropping = False

            # Collect frames after recovery and validate them.
            for i in range(FRAMES_AFTER_RECOVERY):
                frame = await asyncio.wait_for(remote_track.recv(), timeout=15.0)
                assert isinstance(frame, VideoFrame), (
                    f"Post-recovery frame {i} is not a VideoFrame: {type(frame)}"
                )
                assert frame.width > 0, (
                    f"Post-recovery frame {i} has zero width"
                )
                assert frame.height > 0, (
                    f"Post-recovery frame {i} has zero height"
                )
                # The encoder might change resolution slightly but it should
                # be close to 320x240.
                assert frame.width >= 160, (
                    f"Post-recovery frame {i} width too small: {frame.width}"
                )
                assert frame.height >= 120, (
                    f"Post-recovery frame {i} height too small: {frame.height}"
                )

            dropper.uninstall()

        finally:
            await pc_sender.close()
            await pc_receiver.close()

    @asynctest
    async def test_no_loss_baseline(self) -> None:
        """
        Sanity check: without any packet loss, frames should flow continuously.
        This ensures the test infrastructure itself is not broken.
        """
        pc_sender = RTCPeerConnection()
        pc_receiver = RTCPeerConnection()

        receiver_track: list[MediaStreamTrack] = []

        @pc_receiver.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            receiver_track.append(track)

        try:
            sender_track = ColorPatternTrack()
            pc_sender.addTrack(sender_track)

            await negotiate(pc_sender, pc_receiver)
            await wait_ice_completed(pc_sender, pc_receiver, timeout=ICE_SETTLE_TIMEOUT)
            await wait_for(lambda: len(receiver_track) > 0, timeout=5.0)

            remote_track = receiver_track[0]

            num_frames = 10
            frames = []
            for _ in range(num_frames):
                frame = await asyncio.wait_for(remote_track.recv(), timeout=10.0)
                frames.append(frame)

            assert len(frames) == num_frames, (
                f"Expected {num_frames} frames without loss, got {len(frames)}"
            )
            # All frames should be valid VideoFrames.
            for i, f in enumerate(frames):
                assert isinstance(f, VideoFrame), (
                    f"Frame {i} is not a VideoFrame: {type(f)}"
                )

        finally:
            await pc_sender.close()
            await pc_receiver.close()

    @asynctest
    async def test_dropper_actually_blocks_rtp(self) -> None:
        """
        Verify that when the dropper is active, no new frames arrive
        on the receiver side (within a short window).  This proves the
        loss simulation is effective.
        """
        pc_sender = RTCPeerConnection()
        pc_receiver = RTCPeerConnection()

        receiver_track: list[MediaStreamTrack] = []

        @pc_receiver.on("track")
        def on_track(track: MediaStreamTrack) -> None:
            receiver_track.append(track)

        try:
            sender_track = ColorPatternTrack()
            pc_sender.addTrack(sender_track)

            await negotiate(pc_sender, pc_receiver)
            await wait_ice_completed(pc_sender, pc_receiver, timeout=ICE_SETTLE_TIMEOUT)
            await wait_for(lambda: len(receiver_track) > 0, timeout=5.0)

            remote_track = receiver_track[0]

            # Confirm frames are flowing.
            await asyncio.wait_for(remote_track.recv(), timeout=10.0)

            # Enable packet dropping.
            dtls = get_dtls_transport(pc_sender)
            dropper = PacketDropper(dtls)
            dropper.install()
            dropper.dropping = True

            # Try to receive a frame with a short timeout -- it should fail
            # because all RTP packets are being dropped.
            frame_during_loss = None
            try:
                # The jitter buffer / decoder pipeline may still have one
                # frame buffered, so try to drain it.
                while True:
                    frame_during_loss = await asyncio.wait_for(
                        remote_track.recv(), timeout=1.5
                    )
            except asyncio.TimeoutError:
                # This is the expected outcome: no frames arrive.
                pass

            assert dropper.dropped_count > 0, "Expected packets to be dropped"

            # Restore and confirm frames resume.
            dropper.dropping = False
            frame = await asyncio.wait_for(remote_track.recv(), timeout=15.0)
            assert frame is not None, "Frames should resume after dropping stops"

            dropper.uninstall()

        finally:
            await pc_sender.close()
            await pc_receiver.close()
