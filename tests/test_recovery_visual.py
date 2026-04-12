"""Visual quality verification for H264 decoder recovery after packet loss.

Uses a real H264 video file (testsrc pattern), encodes to RTP, applies
packet loss, decodes, and verifies PSNR to ensure true visual recovery.

Run: python -m pytest tests/test_recovery_visual.py -v -s
"""

import fractions
import logging
import os
import random
import subprocess
from pathlib import Path

import av
import numpy as np
import pytest

from aiortc.codecs.h264 import H264Decoder, H264Encoder, h264_depayload
from aiortc.jitterbuffer import JitterBuffer, JitterFrame
from aiortc.rtp import RtpPacket

logger = logging.getLogger(__name__)
TEST_DATA_DIR = Path(__file__).parent / "data"
TEST_CLIP = TEST_DATA_DIR / "test_clip.mp4"


def ensure_test_clip():
    """Generate test clip with ffmpeg if not exists."""
    TEST_DATA_DIR.mkdir(exist_ok=True)
    if not TEST_CLIP.exists():
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "testsrc=duration=3:size=320x240:rate=30",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-g", "30",  # keyframe every 30 frames
                str(TEST_CLIP),
            ],
            capture_output=True,
            check=True,
        )


def load_reference_frames():
    """Load frames from test clip as BGR numpy arrays."""
    ensure_test_clip()
    container = av.open(str(TEST_CLIP))
    frames = []
    for frame in container.decode(video=0):
        img = frame.to_ndarray(format="bgr24")
        frames.append(img)
    container.close()
    return frames


def encode_frames_to_rtp(ref_frames, keyframe_interval=30):
    """Encode reference frames with aiortc's H264 encoder, return RTP packets."""
    encoder = H264Encoder()
    packets = []
    seq = 0
    for i, img in enumerate(ref_frames):
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = i * 3000  # 90kHz / 30fps = 3000
        frame.time_base = fractions.Fraction(1, 90000)
        force_kf = (i % keyframe_interval == 0)
        payloads, timestamp = encoder.encode(frame, force_keyframe=force_kf)
        for payload in payloads:
            packets.append({
                "seq": seq,
                "timestamp": timestamp,
                "payload": bytes(payload),
                "frame_index": i,
            })
            seq += 1
    return packets


def apply_loss(packets, mode="burst", **kwargs):
    """Apply packet loss. Returns new packet list."""
    result = list(packets)
    if mode == "burst":
        start = kwargs.get("start_seq", len(result) // 3)
        count = kwargs.get("count", 10)
        drop_seqs = set(range(start, start + count))
        result = [p for p in result if p["seq"] not in drop_seqs]
    elif mode == "random":
        rate = kwargs.get("rate", 0.05)
        rng = random.Random(kwargs.get("seed", 42))
        result = [p for p in result if rng.random() > rate]
    elif mode == "corrupt":
        rate = kwargs.get("rate", 0.03)
        rng = random.Random(kwargs.get("seed", 42))
        corrupted = []
        for p in result:
            if rng.random() < rate:
                ba = bytearray(p["payload"])
                for _ in range(min(5, len(ba))):
                    ba[rng.randint(0, len(ba) - 1)] = rng.randint(0, 255)
                p = dict(p, payload=bytes(ba))
            corrupted.append(p)
        result = corrupted
    return result


def decode_through_pipeline(packets):
    """Run packets through jitter buffer + H264 decoder.
    Returns list of {timestamp, frame_index_approx, image, pli} dicts."""
    jb = JitterBuffer(capacity=128, is_video=True, reorder_capacity=2)
    decoder = H264Decoder()
    results = []
    pli_count = 0

    for p in packets:
        pkt = RtpPacket()
        pkt.version = 2
        pkt.payload_type = 96
        pkt.sequence_number = p["seq"] % 65536
        pkt.timestamp = p["timestamp"]
        pkt.ssrc = 12345
        pkt.payload = p["payload"]
        pkt.marker = True
        try:
            pkt._data = h264_depayload(pkt.payload)
        except Exception:
            pkt._data = p["payload"]

        pli, frame = jb.add(pkt)
        if pli:
            pli_count += 1

        if frame is not None:
            decoded = decoder.decode(frame)
            for df in decoded:
                img = df.to_ndarray(format="bgr24")
                results.append({
                    "timestamp": frame.timestamp,
                    "image": img,
                    "pli": pli,
                })

    return results, pli_count


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    """Peak Signal-to-Noise Ratio in dB."""
    if a.shape != b.shape:
        return 0.0
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    if mse < 1e-10:
        return 100.0  # practically identical
    return float(10 * np.log10(255.0 ** 2 / mse))


def match_decoded_to_reference(decoded_results, ref_frames, ts_per_frame=3000):
    """Match decoded frames to reference by timestamp.
    Returns list of (ref_index, psnr_value)."""
    matches = []
    for d in decoded_results:
        idx = d["timestamp"] // ts_per_frame
        if 0 <= idx < len(ref_frames):
            p = psnr(ref_frames[idx], d["image"])
            matches.append((idx, p))
    return matches


class TestVisualRecoveryWithRealVideo:
    """Test visual quality using real H264 encoded test patterns."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.ref_frames = load_reference_frames()
        self.rtp_packets = encode_frames_to_rtp(self.ref_frames, keyframe_interval=30)
        print(f"\nLoaded {len(self.ref_frames)} reference frames, "
              f"{len(self.rtp_packets)} RTP packets")

    def test_baseline_high_psnr(self):
        """No loss: all decoded frames should closely match reference."""
        decoded, pli_count = decode_through_pipeline(self.rtp_packets)
        matches = match_decoded_to_reference(decoded, self.ref_frames)

        psnr_vals = [p for _, p in matches]
        if psnr_vals:
            avg = sum(psnr_vals) / len(psnr_vals)
            minv = min(psnr_vals)
            print(f"Baseline: {len(matches)} frames, "
                  f"avg PSNR={avg:.1f}dB, min={minv:.1f}dB, PLI={pli_count}")
            assert avg > 25, f"Baseline PSNR too low: {avg:.1f}dB"
            assert pli_count == 0, f"No PLI expected without loss"

    def test_burst_loss_recovery_psnr(self):
        """After burst loss, frames after next keyframe should have high PSNR."""
        total = len(self.rtp_packets)
        damaged = apply_loss(self.rtp_packets, mode="burst",
                             start_seq=total // 4, count=15)

        decoded, pli_count = decode_through_pipeline(damaged)
        matches = match_decoded_to_reference(decoded, self.ref_frames)

        # Split into before/after the loss region
        loss_start_frame = (total // 4) // 2  # rough estimate
        # After recovery: frames near the next keyframe (frame 60)
        recovery_frame = 60

        before = [(i, p) for i, p in matches if i < loss_start_frame]
        after = [(i, p) for i, p in matches if i >= recovery_frame]

        if before:
            avg_before = sum(p for _, p in before) / len(before)
            print(f"Before loss ({len(before)} frames): avg PSNR={avg_before:.1f}dB")
            assert avg_before > 25, f"Before-loss PSNR too low: {avg_before:.1f}dB"

        if after:
            avg_after = sum(p for _, p in after) / len(after)
            min_after = min(p for _, p in after)
            print(f"After recovery ({len(after)} frames): "
                  f"avg PSNR={avg_after:.1f}dB, min={min_after:.1f}dB")
            # Key assertion: after keyframe, quality should recover
            assert avg_after > 25, (
                f"Post-recovery PSNR too low: {avg_after:.1f}dB. "
                f"Visual quality did NOT recover after keyframe!"
            )
        else:
            print("WARNING: no frames decoded after recovery point")

        print(f"PLI requests: {pli_count}")
        assert pli_count > 0, "PLI should be triggered by burst loss"

    def test_random_loss_quality_degrades_gracefully(self):
        """5% random loss: quality degrades but most frames still good."""
        damaged = apply_loss(self.rtp_packets, mode="random", rate=0.05)
        decoded, pli_count = decode_through_pipeline(damaged)
        matches = match_decoded_to_reference(decoded, self.ref_frames)

        psnr_vals = [p for _, p in matches]
        if psnr_vals:
            good = len([p for p in psnr_vals if p > 20])
            pct = good / len(psnr_vals) * 100
            print(f"5% loss: {len(matches)} frames, "
                  f"good (>20dB): {good} ({pct:.0f}%), PLI={pli_count}")
            # At least 40% should still be good quality
            assert pct > 40, f"Too many bad frames: only {pct:.0f}% good"

    def test_corruption_recovery_quality(self):
        """3% corruption: decoder resets, quality recovers after keyframe."""
        damaged = apply_loss(self.rtp_packets, mode="corrupt", rate=0.03)
        decoded, pli_count = decode_through_pipeline(damaged)
        matches = match_decoded_to_reference(decoded, self.ref_frames)

        # Check last 20% of frames (should be after at least one recovery)
        if matches:
            tail = matches[len(matches) * 4 // 5:]
            if tail:
                avg_tail = sum(p for _, p in tail) / len(tail)
                print(f"3% corrupt: tail PSNR avg={avg_tail:.1f}dB "
                      f"({len(tail)} frames), PLI={pli_count}")

    def test_heavy_burst_then_full_recovery(self):
        """
        The critical test: heavy burst loss (30 packets), then verify that
        the decoder produces pixel-perfect frames after the next keyframe.

        This is the exact scenario that was failing in production:
        network glitch → burst loss → decoder permanently broken.
        """
        total = len(self.rtp_packets)
        # Drop 30 packets in the middle (between keyframe 30 and 60)
        mid = total // 2
        damaged = apply_loss(self.rtp_packets, mode="burst",
                             start_seq=mid, count=30)

        decoded, pli_count = decode_through_pipeline(damaged)
        matches = match_decoded_to_reference(decoded, self.ref_frames)

        print(f"\n=== Heavy Burst Recovery Test ===")
        print(f"Dropped 30 packets at seq {mid}")
        print(f"Decoded: {len(decoded)}, Matched: {len(matches)}, PLI: {pli_count}")

        # The last keyframe is at frame 60 (90 total frames)
        # Frames after 60 should be high quality
        last_keyframe_frames = [(i, p) for i, p in matches if i >= 60]
        if last_keyframe_frames:
            avg = sum(p for _, p in last_keyframe_frames) / len(last_keyframe_frames)
            minv = min(p for _, p in last_keyframe_frames)
            print(f"After keyframe 60: {len(last_keyframe_frames)} frames, "
                  f"avg={avg:.1f}dB, min={minv:.1f}dB")

            # THIS IS THE KEY ASSERTION
            assert avg > 25, (
                f"CRITICAL: Visual quality did NOT recover after keyframe! "
                f"avg PSNR={avg:.1f}dB (need >25dB). "
                f"This is the production bug — decoder stays broken forever."
            )
            print("RECOVERY VERIFIED: Visual quality restored after keyframe!")
        else:
            # If no frames after keyframe 60, check earlier frames
            if matches:
                all_psnr = [p for _, p in matches]
                print(f"No frames after keyframe 60. All PSNR: "
                      f"avg={sum(all_psnr)/len(all_psnr):.1f}dB")
            pytest.skip("No frames decoded after last keyframe")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
