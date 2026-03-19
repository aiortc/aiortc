from collections import OrderedDict

from aiortc.rtcrtpreceiver import TwccTracker
from aiortc.rtcrtpsender import TwccFeedback, TwccPacketResult
from aiortc.rtp import RtcpTwccPacket, _encode_twcc_chunks

from .utils import TestCase


class TwccTrackerTest(TestCase):
    def test_basic(self) -> None:
        """Add packets in order, build feedback, verify results."""
        tracker = TwccTracker()
        # Add 3 packets with 1ms spacing (1000us)
        tracker.add(0, 1000000)
        tracker.add(1, 1001000)
        tracker.add(2, 1002000)

        fb = tracker.build_feedback(ssrc=100, media_ssrc=200)
        self.assertIsNotNone(fb)
        self.assertIsInstance(fb, RtcpTwccPacket)
        self.assertEqual(fb.ssrc, 100)
        self.assertEqual(fb.media_ssrc, 200)
        self.assertEqual(fb.base_sequence_number, 0)
        self.assertEqual(fb.packet_status_count, 3)
        self.assertEqual(fb.feedback_packet_count, 0)
        self.assertEqual(len(fb.packet_results), 3)

        # All should be received (non-None delta)
        for seq, delta in fb.packet_results:
            self.assertIsNotNone(delta)

    def test_out_of_order(self) -> None:
        """Packets arrive out of sequence."""
        tracker = TwccTracker()
        tracker.add(2, 1002000)
        tracker.add(0, 1000000)
        tracker.add(1, 1001000)

        fb = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNotNone(fb)
        self.assertEqual(fb.base_sequence_number, 0)
        self.assertEqual(fb.packet_status_count, 3)
        self.assertEqual(len(fb.packet_results), 3)

        # All received
        for seq, delta in fb.packet_results:
            self.assertIsNotNone(delta)

    def test_seq_num_wraparound(self) -> None:
        """Sequence numbers around 65535->0."""
        tracker = TwccTracker()
        tracker.add(65534, 1000000)
        tracker.add(65535, 1001000)
        tracker.add(0, 1002000)

        fb = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNotNone(fb)
        self.assertEqual(fb.base_sequence_number, 65534)
        self.assertEqual(fb.packet_status_count, 3)
        # All received
        received = sum(1 for _, d in fb.packet_results if d is not None)
        self.assertEqual(received, 3)

    def test_duplicate_ignored(self) -> None:
        """Same sequence number added twice - should be ignored."""
        tracker = TwccTracker()
        tracker.add(0, 1000000)
        tracker.add(0, 1000500)  # duplicate
        tracker.add(1, 1001000)

        fb = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNotNone(fb)
        self.assertEqual(fb.packet_status_count, 2)
        self.assertEqual(len(fb.packet_results), 2)

    def test_empty_returns_none(self) -> None:
        """No packets tracked - build_feedback returns None."""
        tracker = TwccTracker()
        fb = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNone(fb)

    def test_build_clears_state(self) -> None:
        """After building feedback, tracker state is cleared."""
        tracker = TwccTracker()
        tracker.add(0, 1000000)
        tracker.add(1, 1001000)

        fb1 = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNotNone(fb1)
        self.assertEqual(fb1.feedback_packet_count, 0)

        # After building, should be empty
        fb2 = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNone(fb2)

        # Add more packets and build again
        tracker.add(2, 1002000)
        fb3 = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNotNone(fb3)
        self.assertEqual(fb3.feedback_packet_count, 1)

    def test_with_gaps(self) -> None:
        """Packets with gaps (some seq nums missing = lost)."""
        tracker = TwccTracker()
        tracker.add(0, 1000000)
        # seq 1 is missing (lost)
        tracker.add(2, 1002000)
        tracker.add(3, 1003000)
        # seq 4 is missing (lost)
        tracker.add(5, 1005000)

        fb = tracker.build_feedback(ssrc=1, media_ssrc=2)
        self.assertIsNotNone(fb)
        self.assertEqual(fb.base_sequence_number, 0)
        self.assertEqual(fb.packet_status_count, 6)

        received = sum(1 for _, d in fb.packet_results if d is not None)
        lost = sum(1 for _, d in fb.packet_results if d is None)
        self.assertEqual(received, 4)
        self.assertEqual(lost, 2)

        # Verify lost positions
        self.assertIsNotNone(fb.packet_results[0][1])  # seq 0
        self.assertIsNone(fb.packet_results[1][1])  # seq 1 lost
        self.assertIsNotNone(fb.packet_results[2][1])  # seq 2
        self.assertIsNotNone(fb.packet_results[3][1])  # seq 3
        self.assertIsNone(fb.packet_results[4][1])  # seq 4 lost
        self.assertIsNotNone(fb.packet_results[5][1])  # seq 5

    def test_run_length_encoding(self) -> None:
        """Verify chunk encoding produces valid run-length chunks."""
        # All received (status=1)
        statuses = [1] * 20
        data = _encode_twcc_chunks(statuses)
        # Should be a single run-length chunk (2 bytes)
        self.assertEqual(len(data), 2)
        chunk = int.from_bytes(data[0:2], "big")
        self.assertEqual(chunk & 0x8000, 0)  # run-length type
        self.assertEqual((chunk >> 13) & 0x03, 1)  # status=1
        self.assertEqual(chunk & 0x1FFF, 20)  # count=20

        # Mixed statuses
        statuses = [0] * 5 + [1] * 3
        data = _encode_twcc_chunks(statuses)
        # Should be two run-length chunks (4 bytes)
        self.assertEqual(len(data), 4)

    def test_feedback_serialization_roundtrip(self) -> None:
        """Build feedback, serialize, parse, and verify."""
        tracker = TwccTracker()
        for i in range(10):
            tracker.add(i, 1000000 + i * 1000)

        fb = tracker.build_feedback(ssrc=100, media_ssrc=200)
        self.assertIsNotNone(fb)

        # Serialize and parse
        data = bytes(fb)
        from aiortc.rtp import RtcpPacket

        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)
        parsed = self.ensureIsInstance(packets[0], RtcpTwccPacket)
        self.assertEqual(parsed.ssrc, 100)
        self.assertEqual(parsed.media_ssrc, 200)
        self.assertEqual(parsed.base_sequence_number, 0)
        self.assertEqual(parsed.packet_status_count, 10)
        self.assertEqual(len(parsed.packet_results), 10)

        # All received
        received = sum(1 for _, d in parsed.packet_results if d is not None)
        self.assertEqual(received, 10)


class TwccFeedbackTest(TestCase):
    def _make_twcc_packet(
        self, packet_results: list[tuple[int, int | None]]
    ) -> RtcpTwccPacket:
        """Helper to create an RtcpTwccPacket with given packet_results."""
        return RtcpTwccPacket(
            ssrc=1,
            media_ssrc=2,
            base_sequence_number=(packet_results[0][0] if packet_results else 0),
            packet_status_count=len(packet_results),
            reference_time=0,
            feedback_packet_count=0,
            packet_results=packet_results,
        )

    def test_twcc_feedback_construction(self) -> None:
        """Create known send log entries, feed a TwccPacket, verify results."""
        send_log: OrderedDict[int, float] = OrderedDict()
        send_log[10] = 100.0
        send_log[11] = 100.001
        send_log[12] = 100.002

        packet_results = [(10, 500), (11, 1500), (12, 2500)]
        pkt = self._make_twcc_packet(packet_results)

        results = []
        for seq_num, recv_delta_us in pkt.packet_results:
            send_time = send_log.pop(seq_num, None)
            results.append(
                TwccPacketResult(
                    seq=seq_num,
                    send_time=send_time,
                    recv_delta_us=recv_delta_us,
                    lost=recv_delta_us is None,
                )
            )
        feedback = TwccFeedback(packet=pkt, results=results)

        self.assertEqual(len(feedback.results), 3)
        self.assertEqual(feedback.results[0].seq, 10)
        self.assertAlmostEqual(feedback.results[0].send_time, 100.0)
        self.assertEqual(feedback.results[0].recv_delta_us, 500)
        self.assertFalse(feedback.results[0].lost)

        self.assertEqual(feedback.results[2].seq, 12)
        self.assertAlmostEqual(feedback.results[2].send_time, 100.002)
        self.assertEqual(feedback.results[2].recv_delta_us, 2500)
        self.assertFalse(feedback.results[2].lost)

    def test_twcc_feedback_lost_packets(self) -> None:
        """Verify lost=True and recv_delta_us=None for lost packets."""
        send_log: OrderedDict[int, float] = OrderedDict()
        send_log[0] = 100.0
        send_log[1] = 100.001
        send_log[2] = 100.002

        # seq 1 is lost (recv_delta_us=None)
        packet_results = [(0, 500), (1, None), (2, 2500)]
        pkt = self._make_twcc_packet(packet_results)

        results = []
        for seq_num, recv_delta_us in pkt.packet_results:
            send_time = send_log.pop(seq_num, None)
            results.append(
                TwccPacketResult(
                    seq=seq_num,
                    send_time=send_time,
                    recv_delta_us=recv_delta_us,
                    lost=recv_delta_us is None,
                )
            )
        feedback = TwccFeedback(packet=pkt, results=results)

        self.assertFalse(feedback.results[0].lost)
        self.assertTrue(feedback.results[1].lost)
        self.assertIsNone(feedback.results[1].recv_delta_us)
        self.assertAlmostEqual(feedback.results[1].send_time, 100.001)
        self.assertFalse(feedback.results[2].lost)

    def test_twcc_feedback_unknown_seq(self) -> None:
        """Seq not in send log yields send_time=None."""
        send_log: OrderedDict[int, float] = OrderedDict()
        # Only seq 0 is in the log; seq 1 is not
        send_log[0] = 100.0

        packet_results = [(0, 500), (1, 1500)]
        pkt = self._make_twcc_packet(packet_results)

        results = []
        for seq_num, recv_delta_us in pkt.packet_results:
            send_time = send_log.pop(seq_num, None)
            results.append(
                TwccPacketResult(
                    seq=seq_num,
                    send_time=send_time,
                    recv_delta_us=recv_delta_us,
                    lost=recv_delta_us is None,
                )
            )
        feedback = TwccFeedback(packet=pkt, results=results)

        self.assertAlmostEqual(feedback.results[0].send_time, 100.0)
        self.assertIsNone(feedback.results[1].send_time)
        self.assertFalse(feedback.results[1].lost)

    def test_twcc_callback_fires(self) -> None:
        """Register callback, feed TWCC packet, verify it fires."""
        received_feedback = []

        def capture_feedback(feedback: TwccFeedback) -> None:
            received_feedback.append(feedback)

        # Build a TWCC packet
        packet_results = [(5, 1000), (6, None), (7, 3000)]
        pkt = self._make_twcc_packet(packet_results)

        # Simulate what _handle_rtcp_packet does
        send_log: OrderedDict[int, float] = OrderedDict()
        send_log[5] = 200.0
        send_log[6] = 200.001
        send_log[7] = 200.002

        results = []
        for seq_num, recv_delta_us in pkt.packet_results:
            send_time = send_log.pop(seq_num, None)
            results.append(
                TwccPacketResult(
                    seq=seq_num,
                    send_time=send_time,
                    recv_delta_us=recv_delta_us,
                    lost=recv_delta_us is None,
                )
            )
        feedback = TwccFeedback(packet=pkt, results=results)
        capture_feedback(feedback)

        self.assertEqual(len(received_feedback), 1)
        fb = received_feedback[0]
        self.assertIsInstance(fb, TwccFeedback)
        self.assertEqual(len(fb.results), 3)
        self.assertEqual(fb.results[0].seq, 5)
        self.assertFalse(fb.results[0].lost)
        self.assertTrue(fb.results[1].lost)
        self.assertFalse(fb.results[2].lost)
