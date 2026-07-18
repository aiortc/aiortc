"""Mutation fuzzing test for H264 jitter buffer + decoder recovery.

Encodes real video frames with H264, packetizes into RTP, then applies
mutations focused on packet reordering (the primary real-world issue).

Run: python -m pytest tests/test_recovery_fuzz.py -v -s
"""

import asyncio
import fractions
import logging
import queue
import random
import threading
import time

import av
import numpy as np
import pytest

from aiortc.codecs.h264 import H264Decoder, H264Encoder, h264_depayload
from aiortc.jitterbuffer import JitterBuffer, JitterFrame
from aiortc.rtp import RtpPacket
from aiortc.rtcrtpreceiver import decoder_worker

logger = logging.getLogger(__name__)


class FakeCodec:
    name = "h264"
    mimeType = "video/h264"
    clockRate = 90000
    payloadType = 96


def generate_test_frames(width=320, height=240, count=60):
    """Generate test video frames with moving patterns for H264 encoding."""
    frames = []
    for i in range(count):
        img = np.zeros((height, width, 3), dtype=np.uint8)
        img[:, :, 0] = np.linspace(0, 255, width, dtype=np.uint8)
        x = (i * 5) % (width - 50)
        y = (i * 3) % (height - 50)
        img[y : y + 50, x : x + 50] = [0, 255, 0]
        img[10:30, 10:60] = [255, 255, 255]
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = i * 3000
        frame.time_base = fractions.Fraction(1, 90000)
        frames.append(frame)
    return frames


def encode_to_rtp_packets(frames, encoder=None):
    """Encode frames with H264 and return list of (seq, timestamp, payload)."""
    if encoder is None:
        encoder = H264Encoder()
    all_packets = []
    seq = 0
    for i, frame in enumerate(frames):
        force_keyframe = i % 30 == 0
        payloads, timestamp = encoder.encode(frame, force_keyframe=force_keyframe)
        for payload in payloads:
            all_packets.append((seq, timestamp, bytes(payload)))
            seq += 1
    return all_packets


class PacketStream:
    """Simulates an RTP packet stream with configurable mutations."""

    def __init__(self, packets):
        self._packets = list(packets)

    def reorder_window(self, window=3, seed=42):
        """Shuffle packets within a sliding window."""
        rng = random.Random(seed)
        result = []
        buf = []
        for p in self._packets:
            buf.append(p)
            if len(buf) >= window:
                rng.shuffle(buf)
                result.extend(buf)
                buf = []
        result.extend(buf)
        self._packets = result
        return self

    def delay_random(self, delay=5, rate=0.1, seed=42):
        """Delay random packets by N positions (swap with later packet)."""
        rng = random.Random(seed)
        result = list(self._packets)
        for i in range(len(result)):
            if rng.random() < rate:
                j = min(i + delay, len(result) - 1)
                result[i], result[j] = result[j], result[i]
        self._packets = result
        return self

    def swap_adjacent(self):
        """Swap every pair of adjacent packets."""
        result = []
        for i in range(0, len(self._packets) - 1, 2):
            result.append(self._packets[i + 1])
            result.append(self._packets[i])
        if len(self._packets) % 2:
            result.append(self._packets[-1])
        self._packets = result
        return self

    def reverse_bursts(self, burst_size=5, interval=20, seed=42):
        """Reverse the order of packets in periodic bursts."""
        rng = random.Random(seed)
        result = list(self._packets)
        for start in range(0, len(result), interval):
            if rng.random() < 0.5:
                end = min(start + burst_size, len(result))
                result[start:end] = reversed(result[start:end])
        self._packets = result
        return self

    def drop_random(self, rate=0.05, seed=42):
        """Drop packets randomly."""
        rng = random.Random(seed)
        self._packets = [p for p in self._packets if rng.random() > rate]
        return self

    def drop_burst(self, start_seq, count):
        """Drop consecutive packets."""
        drop = set(range(start_seq, start_seq + count))
        self._packets = [p for p in self._packets if p[0] not in drop]
        return self

    def corrupt_random(self, rate=0.02, seed=42):
        """Corrupt random packet payloads."""
        rng = random.Random(seed)
        corrupted = []
        for seq, ts, payload in self._packets:
            if rng.random() < rate:
                ba = bytearray(payload)
                for _ in range(min(5, len(ba))):
                    ba[rng.randint(0, len(ba) - 1)] = rng.randint(0, 255)
                payload = bytes(ba)
            corrupted.append((seq, ts, payload))
        self._packets = corrupted
        return self

    def to_rtp_packets(self):
        """Convert to RTP packet objects."""
        rtp_packets = []
        for seq, ts, payload in self._packets:
            pkt = RtpPacket()
            pkt.version = 2
            pkt.payload_type = 96
            pkt.sequence_number = seq % 65536
            pkt.timestamp = ts
            pkt.ssrc = 12345
            pkt.payload = payload
            pkt.marker = True
            try:
                pkt._data = h264_depayload(pkt.payload)
            except Exception:
                pkt._data = payload
            rtp_packets.append(pkt)
        return rtp_packets


def run_through_pipeline(rtp_packets, reorder_capacity=8):
    """Run through jitter buffer + decoder, return stats."""
    jb = JitterBuffer(capacity=128, is_video=True, reorder_capacity=reorder_capacity)
    decoder = H264Decoder()
    stats = {
        "packets_in": len(rtp_packets),
        "frames_assembled": 0,
        "frames_decoded": 0,
        "decode_errors": 0,
        "pli_requests": 0,
        "recovery_after_pli": False,
        "decode_timeline": [],
    }
    for i, pkt in enumerate(rtp_packets):
        pli, frame = jb.add(pkt)
        if pli:
            stats["pli_requests"] += 1
        if frame is not None:
            stats["frames_assembled"] += 1
            result = decoder.decode(frame)
            if result:
                stats["frames_decoded"] += 1
                stats["decode_timeline"].append((i, True))
                if stats["decode_errors"] > 0:
                    stats["recovery_after_pli"] = True
            else:
                stats["decode_errors"] += 1
                stats["decode_timeline"].append((i, False))
    return stats


class TestReorderRecovery:
    """Test jitter buffer + decoder recovery with packet reordering mutations."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.frames = generate_test_frames(width=320, height=240, count=90)
        self.packets = encode_to_rtp_packets(self.frames)
        print(f"\nEncoded {len(self.frames)} frames → {len(self.packets)} RTP packets")

    def test_baseline(self):
        """No mutation: all frames decode."""
        stream = PacketStream(self.packets)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Baseline: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}")
        assert stats["frames_decoded"] > 0
        assert stats["pli_requests"] == 0

    def test_adjacent_swap(self):
        """Every adjacent pair swapped — reorder_capacity=8 should handle."""
        stream = PacketStream(self.packets).swap_adjacent()
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Adjacent swap: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 80, "Adjacent swap should be fully absorbed"

    def test_reorder_window_3(self):
        """Window=3 shuffle."""
        stream = PacketStream(self.packets).reorder_window(window=3)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Window 3: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 70, "Window 3 should be well handled"

    def test_reorder_window_5(self):
        """Window=5 shuffle — within reorder_capacity=8."""
        stream = PacketStream(self.packets).reorder_window(window=5)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Window 5: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 60, "Window 5 should be mostly handled with capacity=8"

    def test_reorder_window_8(self):
        """Window=8 shuffle — at the limit of reorder_capacity=8."""
        stream = PacketStream(self.packets).reorder_window(window=8)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Window 8: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 0, "Window 8 should still produce some frames"

    def test_10pct_delayed_by_5(self):
        """10% of packets delayed by 5 positions."""
        stream = PacketStream(self.packets).delay_random(delay=5, rate=0.1)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"10% delay 5: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 40, "10% delay by 5 should be recoverable"

    def test_reverse_bursts(self):
        """Periodic reverse bursts of 5 packets every 20."""
        stream = PacketStream(self.packets).reverse_bursts(burst_size=5, interval=20)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Reverse bursts: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 30, "Periodic reverse should be partially recoverable"

    def test_reorder_plus_drop(self):
        """Reorder window=5 + 2% drop — realistic bad network."""
        stream = PacketStream(self.packets).reorder_window(window=5).drop_random(rate=0.02)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Reorder+drop: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 0, "Combined reorder+drop should still produce frames"

    def test_reorder_plus_corrupt(self):
        """Reorder window=3 + 2% corruption — realistic bad network."""
        stream = PacketStream(self.packets).reorder_window(window=3).corrupt_random(rate=0.02)
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Reorder+corrupt: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")
        assert stats["frames_decoded"] > 0, "Combined reorder+corrupt should still produce frames"

    def test_heavy_reorder_recovery(self):
        """
        KEY TEST: Heavy reorder (window=10) followed by normal flow.
        After the chaos, decoder should recover when clean packets arrive.
        """
        # First half: heavy reorder
        mid = len(self.packets) // 2
        first_half = self.packets[:mid]
        second_half = self.packets[mid:]

        rng = random.Random(42)
        buf = []
        reordered = []
        for p in first_half:
            buf.append(p)
            if len(buf) >= 10:
                rng.shuffle(buf)
                reordered.extend(buf)
                buf = []
        reordered.extend(buf)
        # Second half: clean
        all_pkts = reordered + second_half

        stream = PacketStream(all_pkts)
        stats = run_through_pipeline(stream.to_rtp_packets())

        print(f"\n=== Heavy Reorder Recovery ===")
        print(f"decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")

        # The second half (clean) should produce decoded frames
        assert stats["frames_decoded"] > 20, (
            f"Should decode frames in the clean second half. "
            f"Decoded only {stats['frames_decoded']}"
        )

    def test_multi_seed_fuzz(self):
        """Run reorder fuzzing with multiple random seeds for stability."""
        results = []
        for seed in range(10):
            stream = PacketStream(self.packets).reorder_window(window=5, seed=seed)
            stats = run_through_pipeline(stream.to_rtp_packets())
            results.append(stats["frames_decoded"])

        avg = sum(results) / len(results)
        minv = min(results)
        print(f"Multi-seed (10 runs, window=5): avg={avg:.0f}, min={minv}, all={results}")
        assert avg > 30, f"Average decoded too low: {avg:.0f}"

    def test_burst_loss_then_reorder(self):
        """Burst loss followed by reordered packets — combined stress."""
        mid = len(self.packets) // 3
        stream = (
            PacketStream(self.packets)
            .drop_burst(mid, 15)
            .reorder_window(window=5)
        )
        stats = run_through_pipeline(stream.to_rtp_packets())
        print(f"Burst+reorder: decoded={stats['frames_decoded']}, errors={stats['decode_errors']}, PLI={stats['pli_requests']}")


class TestDecoderWorkerRecoveryFuzz:
    """Test decoder_worker PLI signaling."""

    def test_worker_pli_on_decode_errors(self):
        """Feed corrupted H264 data to decoder_worker, verify PLI is signaled."""
        loop = asyncio.new_event_loop()
        input_q = queue.Queue()
        output_q = asyncio.Queue()
        pli_event = threading.Event()
        codec = FakeCodec()

        t = threading.Thread(
            target=decoder_worker,
            args=(loop, input_q, output_q),
            kwargs={"pli_event": pli_event},
            daemon=True,
        )
        t.start()

        for i in range(5):
            bad_frame = JitterFrame(
                data=b"\x00\x00\x01\x65" + bytes(range(100)),
                timestamp=i * 3000,
            )
            input_q.put((codec, bad_frame))

        time.sleep(1)
        print(f"PLI event set: {pli_event.is_set()}")
        input_q.put(None)
        t.join(timeout=2)
        loop.close()
        assert pli_event.is_set(), "PLI should be signaled after decode errors"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
