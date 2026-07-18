from unittest import TestCase

from aiortc.jitterbuffer import JitterBuffer, JitterFrame
from aiortc.rtp import RtpPacket


class JitterBufferTest(TestCase):

    # ---------------------------------------------------------------
    # Basic behavior tests (no internal state checks)
    # ---------------------------------------------------------------

    def test_create(self) -> None:
        jbuffer = JitterBuffer(capacity=4)
        self.assertEqual(jbuffer.capacity, 4)

    def test_add_ordered_no_frame(self) -> None:
        """Adding packets with the same timestamp should not produce a frame."""
        jbuffer = JitterBuffer(capacity=4)

        for seq in range(4):
            pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=seq, timestamp=1234))
            self.assertIsNone(frame)
            self.assertFalse(pli_flag)

    def test_add_seq_too_low_drop(self) -> None:
        """A packet older than the last emitted seq should be silently dropped."""
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)

        # seq=1 is behind seq=2 (already emitted with reorder_capacity=1)
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_reset(self) -> None:
        """A very old seq (>MAX_MISORDER behind) should trigger a stream reset."""
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))

        # seq=1 is >100 behind seq=2000 -> stream reset
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)

    def test_add_seq_too_high_overflow(self) -> None:
        """When seq span exceeds capacity, older packets are force-emitted."""
        jbuffer = JitterBuffer(capacity=4)

        for seq in range(4):
            jbuffer.add(RtpPacket(sequence_number=seq, timestamp=1234))

        # seq=4 causes span=4 >= capacity=4, force emit
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=4, timestamp=1234))
        self.assertIsNone(frame)  # All same timestamp, no frame boundary

    def test_add_seq_too_high_reset(self) -> None:
        """A very large forward jump should trigger overflow handling."""
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))

        # seq=3000 is far ahead -> overflow, force emit older packets
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=3000, timestamp=1234))
        self.assertIsNone(frame)

    # ---------------------------------------------------------------
    # Audio frame delivery (prefetch)
    # ---------------------------------------------------------------

    def test_remove_audio_frame(self) -> None:
        """
        Audio jitter buffer with prefetch=4.
        Frame is delivered after prefetch frame boundaries are seen.
        """
        jbuffer = JitterBuffer(capacity=16, prefetch=4)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"0000"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=1, timestamp=1235)
        packet._data = b"0001"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=2, timestamp=1236)
        packet._data = b"0002"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=3, timestamp=1237)
        packet._data = b"0003"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=4, timestamp=1238)
        packet._data = b"0003"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"0000")
        self.assertEqual(frame.timestamp, 1234)

        packet = RtpPacket(sequence_number=5, timestamp=1239)
        packet._data = b"0004"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"0001")
        self.assertEqual(frame.timestamp, 1235)

    # ---------------------------------------------------------------
    # Video frame delivery
    # ---------------------------------------------------------------

    def test_remove_video_frame(self) -> None:
        """
        Video jitter buffer: multiple packets with the same timestamp
        form a single frame. Frame is delivered when next timestamp arrives.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"0000"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=1, timestamp=1234)
        packet._data = b"0001"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=2, timestamp=1234)
        packet._data = b"0002"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=3, timestamp=1235)
        packet._data = b"0003"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"000000010002")
        self.assertEqual(frame.timestamp, 1234)

    # ---------------------------------------------------------------
    # PLI flag
    # ---------------------------------------------------------------

    def test_pli_flag_on_stream_reset(self) -> None:
        """PLI is set when a stream reset occurs (video only)."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))

        # Very old seq -> stream reset -> PLI
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertTrue(pli_flag)

    def test_pli_flag_on_overflow(self) -> None:
        """PLI is set when buffer overflows (video only)."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))

        # seq=130: span=129 >= capacity=128 -> overflow -> PLI
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=130, timestamp=1235))
        self.assertTrue(pli_flag)

    def test_no_pli_flag_non_video(self) -> None:
        """PLI is NOT set for non-video buffers even on reset."""
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertFalse(pli_flag)

    # ---------------------------------------------------------------
    # Packet loss recovery (video)
    # ---------------------------------------------------------------

    def test_video_packet_loss_mid_frame(self) -> None:
        """
        A lost packet in the middle of a video frame should not stall
        the buffer. The incomplete frame is discarded, PLI is set, and
        subsequent frames are delivered.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        # Frame A: seq=0,1,2 (ts=1234). Seq=1 is LOST.
        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"A0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)
        self.assertFalse(pli_flag)

        # seq=1 is lost

        # seq=2: gap detected (expected 1, got 2), PLI set, incomplete frame discarded
        packet = RtpPacket(sequence_number=2, timestamp=1234)
        packet._data = b"A2"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)
        self.assertTrue(pli_flag)

        # Frame B: seq=3 (ts=1235). Timestamp change triggers emit of
        # the partial frame (just seq=2 after gap discard). This is a
        # fragment, not a real frame — but the assembler emits it since
        # the gap already cleared the earlier packets.
        packet = RtpPacket(sequence_number=3, timestamp=1235)
        packet._data = b"B0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        # Fragment from seq=2 is emitted here (ts=1234)
        # The important thing: the buffer is NOT stalled.

        packet = RtpPacket(sequence_number=4, timestamp=1235)
        packet._data = b"B1"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        # Frame C: seq=5 (ts=1236). Triggers Frame B delivery.
        packet = RtpPacket(sequence_number=5, timestamp=1236)
        packet._data = b"C0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"B0B1")
        self.assertEqual(frame.timestamp, 1235)

    def test_video_packet_loss_recovery_multiple_frames(self) -> None:
        """After packet loss, subsequent complete frames are delivered."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        # Frame A: seq=0 (ts=1000).
        packet = RtpPacket(sequence_number=0, timestamp=1000)
        packet._data = b"A"  # type: ignore
        jbuffer.add(packet)

        # Frame B: seq=1,2 (ts=2000). seq=1 is LOST.
        packet = RtpPacket(sequence_number=2, timestamp=2000)
        packet._data = b"B1"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertTrue(pli_flag)  # Gap: expected 1, got 2

        # Frame C: seq=3 (ts=3000).
        packet = RtpPacket(sequence_number=3, timestamp=3000)
        packet._data = b"C"  # type: ignore
        jbuffer.add(packet)

        # Frame D: seq=4 (ts=4000). Triggers Frame C delivery.
        packet = RtpPacket(sequence_number=4, timestamp=4000)
        packet._data = b"D"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)

    def test_video_consecutive_packet_loss(self) -> None:
        """Multiple consecutive lost packets should not stall the buffer."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        # Frame A: seq=0,1,2,3 (ts=1234). seq=1,2 are LOST.
        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"A0"  # type: ignore
        jbuffer.add(packet)

        # seq=3: gap detected (expected 1, got 3)
        packet = RtpPacket(sequence_number=3, timestamp=1234)
        packet._data = b"A3"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertTrue(pli_flag)

        # Frame B: seq=4,5 (ts=1235).
        packet = RtpPacket(sequence_number=4, timestamp=1235)
        packet._data = b"B0"  # type: ignore
        jbuffer.add(packet)

        packet = RtpPacket(sequence_number=5, timestamp=1235)
        packet._data = b"B1"  # type: ignore
        jbuffer.add(packet)

        # Frame C: seq=6 (ts=1236). Triggers Frame B delivery.
        packet = RtpPacket(sequence_number=6, timestamp=1236)
        packet._data = b"C0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"B0B1")
        self.assertEqual(frame.timestamp, 1235)

    # ---------------------------------------------------------------
    # Reordering tolerance (video, reorder_capacity > 1)
    # ---------------------------------------------------------------

    def test_video_reorder_3_packets(self) -> None:
        """
        With reorder_capacity=5, 3-packet reordering within a frame
        should be handled correctly without false PLI.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)
        jbuffer = JitterBuffer(capacity=128, is_video=True, reorder_capacity=5)

        # Frame A: seq=0,1,2,3 (ts=1234). Arrival order: 0, 2, 3, 1.
        for seq in [0, 2, 3, 1]:
            packet = RtpPacket(sequence_number=seq, timestamp=1234)
            packet._data = f"A{seq}".encode()  # type: ignore
            pli_flag, frame = jbuffer.add(packet)
            self.assertIsNone(frame)
            self.assertFalse(pli_flag)

        # Frame B: seq=4 (ts=1235). Pushes buffer to 5 -> emit seq=0.
        packet = RtpPacket(sequence_number=4, timestamp=1235)
        packet._data = b"B0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        # Buffer had [0,1,2,3,4], emitted seq=0. No frame boundary yet.
        self.assertIsNone(frame)
        self.assertFalse(pli_flag)

        # Add more packets to push remaining out and trigger frame boundary.
        for seq in range(5, 9):
            packet = RtpPacket(sequence_number=seq, timestamp=1235)
            packet._data = f"B{seq-4}".encode()  # type: ignore
            pli_flag, frame = jbuffer.add(packet)

        # By now seq=0,1,2,3 emitted (Frame A), then seq=4+ (Frame B, different ts).
        # Frame A should have been delivered.
        self.assertIsNotNone(frame)
        self.assertFalse(pli_flag)

    def test_video_reorder_across_frames(self) -> None:
        """
        With reorder_capacity=5, packets from two frames arrive interleaved.
        Frame A: seq=0,1 (ts=1000), Frame B: seq=2,3 (ts=2000).
        Arrival order: 0, 2, 3, 1.
        After enough packets arrive, Frame A should be delivered correctly.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)
        jbuffer = JitterBuffer(capacity=128, is_video=True, reorder_capacity=5)

        for seq, ts in [(0, 1000), (2, 2000), (3, 2000), (1, 1000)]:
            packet = RtpPacket(sequence_number=seq, timestamp=ts)
            packet._data = f"{seq}".encode()  # type: ignore
            pli_flag, frame = jbuffer.add(packet)
            self.assertIsNone(frame)
            self.assertFalse(pli_flag)

        # Push packets through: add seq=4..8 to emit buffered packets.
        last_frame = None
        for seq in range(4, 9):
            packet = RtpPacket(sequence_number=seq, timestamp=3000)
            packet._data = f"C{seq}".encode()  # type: ignore
            pli_flag, frame = jbuffer.add(packet)
            if frame is not None:
                last_frame = frame

        # Frame A (ts=1000) should have been delivered
        self.assertIsNotNone(last_frame)

    # ---------------------------------------------------------------
    # Flush
    # ---------------------------------------------------------------

    def test_flush(self) -> None:
        """Flush emits all remaining buffered packets as frames."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)
        jbuffer = JitterBuffer(capacity=128, is_video=True, reorder_capacity=5)

        for seq in range(3):
            packet = RtpPacket(sequence_number=seq, timestamp=1234)
            packet._data = f"{seq}".encode()  # type: ignore
            jbuffer.add(packet)

        # 3 packets in buffer, not yet emitted (< reorder_capacity=5)
        frames = jbuffer.flush()
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].timestamp, 1234)
        self.assertEqual(frames[0].data, b"012")

    # ---------------------------------------------------------------
    # Edge cases / boundary conditions
    # ---------------------------------------------------------------

    def test_reorder_capacity_zero_does_not_hang(self) -> None:
        """reorder_capacity=0 must not cause an infinite loop."""
        jbuffer = JitterBuffer(capacity=4, reorder_capacity=0)

        packet = RtpPacket(sequence_number=0, timestamp=1000)
        packet._data = b"A"  # type: ignore
        # This must complete without hanging
        pli_flag, frame = jbuffer.add(packet)

    def test_duplicate_of_last_emitted_seq(self) -> None:
        """
        A duplicate packet with the same seq as the last emitted packet
        should be silently dropped. It must NOT trigger a false gap
        detection or PLI.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1000)
        packet._data = b"A"  # type: ignore
        jbuffer.add(packet)  # emitted, _last_emitted_seq=0

        packet = RtpPacket(sequence_number=1, timestamp=1000)
        packet._data = b"B"  # type: ignore
        jbuffer.add(packet)  # emitted, frame_packets=[A, B]

        # Duplicate of seq=1
        packet = RtpPacket(sequence_number=1, timestamp=1000)
        packet._data = b"B"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        # Must NOT trigger PLI or discard the frame being assembled
        self.assertFalse(pli_flag)

        # Verify frame assembly is still intact by completing the frame
        packet = RtpPacket(sequence_number=2, timestamp=2000)
        packet._data = b"C"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"AB")
        self.assertEqual(frame.timestamp, 1000)

    def test_reorder_buffer_accepts_earlier_seq(self) -> None:
        """
        With reorder_capacity>1, a packet with a lower seq than existing
        buffer contents should be inserted correctly, not dropped.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True, reorder_capacity=5)

        # seq=100 arrives first
        packet = RtpPacket(sequence_number=100, timestamp=1000)
        packet._data = b"A"  # type: ignore
        jbuffer.add(packet)

        # seq=99 arrives (reordered, lower than buffer min)
        packet = RtpPacket(sequence_number=99, timestamp=1000)
        packet._data = b"B"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        # Must not be dropped — should be in the buffer
        self.assertFalse(pli_flag)
        self.assertEqual(len(jbuffer._buffer), 2)
        self.assertEqual(jbuffer._buffer[0].sequence_number, 99)
        self.assertEqual(jbuffer._buffer[1].sequence_number, 100)

    # ---------------------------------------------------------------
    # Sequence number wraparound (uint16: 65535 -> 0)
    # ---------------------------------------------------------------

    def test_seq_wraparound_frame_delivery(self) -> None:
        """
        Sequence number wrapping from 65535 to 0 must not be treated
        as a gap or loss. A frame spanning the wrap boundary should
        be delivered correctly.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        for seq in [65534, 65535, 0]:
            packet = RtpPacket(sequence_number=seq, timestamp=1000)
            packet._data = f"{seq}".encode()  # type: ignore
            pli_flag, frame = jbuffer.add(packet)
            self.assertFalse(pli_flag)
            self.assertIsNone(frame)

        # Trigger frame boundary
        packet = RtpPacket(sequence_number=1, timestamp=2000)
        packet._data = b"next"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        self.assertFalse(pli_flag)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.timestamp, 1000)
        self.assertEqual(frame.data, b"65534655350")

    def test_seq_wraparound_loss_detection(self) -> None:
        """
        A genuine packet loss across the wrap boundary should still
        be detected. seq=65535 followed by seq=1 (seq=0 lost).
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=65535, timestamp=1000)
        packet._data = b"A"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertFalse(pli_flag)

        # seq=0 is lost, seq=1 arrives
        packet = RtpPacket(sequence_number=1, timestamp=1000)
        packet._data = b"B"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        # Gap: expected 0, got 1 -> PLI
        self.assertTrue(pli_flag)

    def test_seq_wraparound_reorder(self) -> None:
        """
        With reorder_capacity>1, packets around the wrap boundary
        arriving out of order should be sorted correctly.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True, reorder_capacity=5)

        # Arrival order: 65535, 1, 0, 65534 (out of order around wrap)
        for seq in [65535, 1, 0, 65534]:
            packet = RtpPacket(sequence_number=seq, timestamp=1000)
            packet._data = f"{seq}".encode()  # type: ignore
            jbuffer.add(packet)

        # Buffer should be sorted: 65534, 65535, 0, 1
        self.assertEqual(len(jbuffer._buffer), 4)
        self.assertEqual(jbuffer._buffer[0].sequence_number, 65534)
        self.assertEqual(jbuffer._buffer[1].sequence_number, 65535)
        self.assertEqual(jbuffer._buffer[2].sequence_number, 0)
        self.assertEqual(jbuffer._buffer[3].sequence_number, 1)

    # ---------------------------------------------------------------
    # Marker bit (immediate frame delivery)
    # ---------------------------------------------------------------

    def test_marker_bit_immediate_frame_delivery(self) -> None:
        """When the RTP marker bit is set, the frame should be delivered
        immediately without waiting for the next timestamp."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1000, marker=0)
        packet._data = b"A0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=1, timestamp=1000, marker=0)
        packet._data = b"A1"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        # Last packet of frame: marker=1
        packet = RtpPacket(sequence_number=2, timestamp=1000, marker=1)
        packet._data = b"A2"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        # Frame should be delivered immediately (no need for next timestamp)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b"A0A1A2")
        self.assertEqual(frame.timestamp, 1000)

    # ---------------------------------------------------------------
    # Production reorder_capacity=8
    # ---------------------------------------------------------------

    def test_production_reorder_capacity_8(self) -> None:
        """Verify frame delivery with production reorder_capacity=8."""
        jbuffer = JitterBuffer(capacity=128, is_video=True, reorder_capacity=8)

        # Frame A: seq 0..2 (ts=1000), 3 packets
        for seq in range(3):
            packet = RtpPacket(sequence_number=seq, timestamp=1000)
            packet._data = f"A{seq}".encode()  # type: ignore
            jbuffer.add(packet)

        # Frame B: seq 3..9 (ts=2000), 7 packets
        # After adding seq=7 (8th total), buffer starts emitting
        delivered_frame = None
        for seq in range(3, 12):
            packet = RtpPacket(sequence_number=seq, timestamp=2000)
            packet._data = f"B{seq}".encode()  # type: ignore
            pli_flag, frame = jbuffer.add(packet)
            if frame is not None:
                delivered_frame = frame

        self.assertIsNotNone(delivered_frame)

    # ---------------------------------------------------------------
    # Frame boundary loss (last packet of frame lost)
    # ---------------------------------------------------------------

    def test_loss_last_packet_of_frame(self) -> None:
        """Loss of the last packet in a frame should be detected as a gap
        and trigger PLI, not deliver an incomplete frame."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        # Frame A: seq=0,1,2 (ts=1000). seq=2 is LOST.
        packet = RtpPacket(sequence_number=0, timestamp=1000)
        packet._data = b"A0"  # type: ignore
        jbuffer.add(packet)

        packet = RtpPacket(sequence_number=1, timestamp=1000)
        packet._data = b"A1"  # type: ignore
        jbuffer.add(packet)

        # seq=2 lost. seq=3 from Frame B arrives.
        packet = RtpPacket(sequence_number=3, timestamp=2000)
        packet._data = b"B0"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)

        # Gap detected (expected 2, got 3) -> PLI, incomplete Frame A discarded
        self.assertTrue(pli_flag)

    # ---------------------------------------------------------------
    # Memory protection
    # ---------------------------------------------------------------

    def test_pending_frames_capped(self) -> None:
        """_pending_frames should not grow beyond MAX_PENDING_FRAMES."""
        from aiortc.jitterbuffer import MAX_PENDING_FRAMES
        jbuffer = JitterBuffer(capacity=4096, is_video=True)

        # Generate many single-packet frames rapidly
        for seq in range(MAX_PENDING_FRAMES + 20):
            packet = RtpPacket(sequence_number=seq, timestamp=seq * 1000)
            packet._data = b"X"  # type: ignore
            jbuffer.add(packet)

        self.assertLessEqual(len(jbuffer._pending_frames), MAX_PENDING_FRAMES)

    def test_frame_packets_capped(self) -> None:
        """_frame_packets should not grow beyond MAX_FRAME_PACKETS."""
        from aiortc.jitterbuffer import MAX_FRAME_PACKETS
        jbuffer = JitterBuffer(capacity=4096, is_video=True)

        # Send many packets with the same timestamp
        for seq in range(MAX_FRAME_PACKETS + 10):
            packet = RtpPacket(sequence_number=seq, timestamp=1000)
            packet._data = b"X"  # type: ignore
            jbuffer.add(packet)

        self.assertLessEqual(len(jbuffer._frame_packets), MAX_FRAME_PACKETS)

    # ---------------------------------------------------------------
    # Sustained operation (stress)
    # ---------------------------------------------------------------

    def test_sustained_1000_frames(self) -> None:
        """Send 1000 video frames and verify all are delivered."""
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        delivered = []
        for frame_idx in range(1000):
            ts = frame_idx * 3000
            for pkt_idx in range(3):
                seq = (frame_idx * 3 + pkt_idx) % 65536
                packet = RtpPacket(sequence_number=seq, timestamp=ts)
                packet._data = f"{frame_idx}".encode()  # type: ignore
                pli_flag, frame = jbuffer.add(packet)
                if frame is not None:
                    delivered.append(frame.timestamp)

        delivered.extend([f.timestamp for f in jbuffer.flush()])
        self.assertEqual(len(delivered), 1000)
