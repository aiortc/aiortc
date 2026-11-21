from typing import Optional
from unittest import TestCase

from aiortc.jitterbuffer import JitterBuffer
from aiortc.rtp import RtpPacket


class JitterBufferTest(TestCase):
    def assertPackets(
        self, jbuffer: JitterBuffer, expected: list[Optional[int]]
    ) -> None:
        found = [x.sequence_number if x else None for x in jbuffer._packets]
        self.assertEqual(found, expected)

    def test_create(self) -> None:
        jbuffer = JitterBuffer(capacity=2)
        self.assertEqual(jbuffer._packets, [None, None])
        self.assertEqual(jbuffer._origin, None)

        jbuffer = JitterBuffer(capacity=4)
        self.assertEqual(jbuffer._packets, [None, None, None, None])
        self.assertEqual(jbuffer._origin, None)

    def test_add_ordered(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, None, None, None])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, 1, None, None])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, 1, 2, None])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, 1, 2, 3])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

    def test_add_unordered(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, None, None])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, None, 3])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, 2, 3])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_drop(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, None, 2, None])
        self.assertEqual(jbuffer._origin, 2)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, None, 2, None])
        self.assertEqual(jbuffer._origin, 2)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_reset(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [2000, None, None, None])
        self.assertEqual(jbuffer._origin, 2000)
        self.assertFalse(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, None, None])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

    def test_add_seq_too_high_discard_one(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=4, timestamp=1234))
        self.assertEqual(jbuffer._origin, 4)

        self.assertPackets(jbuffer, [4, None, None, None])

    def test_add_seq_too_high_discard_one_v2(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1235))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=4, timestamp=1235))
        self.assertEqual(jbuffer._origin, 3)

        self.assertPackets(jbuffer, [4, None, None, 3])

    def test_add_seq_too_high_discard_four(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=7, timestamp=1235))
        self.assertEqual(jbuffer._origin, 7)

        self.assertPackets(jbuffer, [None, None, None, 7])

    def test_add_seq_too_high_discard_more(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=8, timestamp=1234))
        self.assertEqual(jbuffer._origin, 8)

        self.assertPackets(jbuffer, [8, None, None, None])

    def test_add_seq_too_high_reset(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        self.assertPackets(jbuffer, [0, None, None, None])

        jbuffer.add(RtpPacket(sequence_number=3000, timestamp=1234))
        self.assertEqual(jbuffer._origin, 3000)
        self.assertPackets(jbuffer, [3000, None, None, None])

    def test_remove(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        self.assertPackets(jbuffer, [0, 1, 2, 3])

        # remove 1 packet
        jbuffer.remove(1)
        self.assertEqual(jbuffer._origin, 1)
        self.assertPackets(jbuffer, [None, 1, 2, 3])

        # remove 2 packets
        jbuffer.remove(2)
        self.assertEqual(jbuffer._origin, 3)
        self.assertPackets(jbuffer, [None, None, None, 3])

    def test_smart_remove(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1235))
        self.assertEqual(jbuffer._origin, 0)
        self.assertPackets(jbuffer, [0, 1, None, 3])

        # remove 1 packet
        jbuffer.smart_remove(1)
        self.assertEqual(jbuffer._origin, 3)
        self.assertPackets(jbuffer, [None, None, None, 3])

    def test_remove_audio_frame(self) -> None:
        """
        Audio jitter buffer.
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

    def test_remove_video_frame(self) -> None:
        """
        Video jitter buffer.
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

    def test_lost_packet_reset(self) -> None:
        jbuffer = JitterBuffer(capacity=4, is_video=True, lost_after=1)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))

        # Dropping the frame of the lost packet empties the buffer.
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1235))
        self.assertIsNone(frame)
        self.assertTrue(pli_flag)
        self.assertEqual(jbuffer._origin, 3)
        self.assertPackets(jbuffer, [None, None, None, 3])

    def test_lost_packets(self) -> None:
        jbuffer = JitterBuffer(capacity=16, is_video=True, lost_after=4)
        for sequence_number, timestamp in [(0, 1234), (2, 1235), (4, 1236)]:
            jbuffer.add(RtpPacket(sequence_number=sequence_number, timestamp=timestamp))

        # Packets 1 and 3 are both too old to be retransmitted.
        packet = RtpPacket(sequence_number=9, timestamp=1237)
        packet._data = b"0009"  # type: ignore
        pli_flag, frame = jbuffer.add(packet)
        self.assertIsNone(frame)
        self.assertTrue(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=10, timestamp=1238))
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.data, b"0009")
        self.assertEqual(frame.timestamp, 1237)
        self.assertTrue(pli_flag)

    def test_lost_packets_late_packet(self) -> None:
        jbuffer = JitterBuffer(capacity=16, is_video=True, lost_after=4)
        for sequence_number in [0, 2, 16]:
            jbuffer.add(
                RtpPacket(sequence_number=sequence_number, timestamp=sequence_number)
            )

        # The drop moves past this late packet: it must not be stored.
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=12, timestamp=12))
        self.assertIsNone(frame)
        self.assertTrue(pli_flag)
        self.assertEqual(jbuffer._origin, 16)
        self.assertPackets(jbuffer, [16] + [None] * 15)

    def test_pli_flag(self) -> None:
        """
        Video jitter buffer.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))
        self.assertIsNone(frame)
        self.assertEqual(jbuffer._origin, 2000)
        self.assertFalse(pli_flag)

        # test_add_seq_too_low_reset for video (capacity >= 128)
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertEqual(jbuffer._origin, 1)
        self.assertTrue(pli_flag)

        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=128, timestamp=1235))
        self.assertIsNone(frame)
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

        # test_add_seq_too_high_discard_one for video (capacity >= 128)
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=129, timestamp=1235))
        self.assertIsNone(frame)
        self.assertEqual(jbuffer._origin, 128)
        self.assertTrue(pli_flag)

        # test_add_seq_too_high_reset for video (capacity >= 128)
        pli_flag, frame = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=2345))
        self.assertIsNone(frame)
        self.assertEqual(jbuffer._origin, 2000)
        self.assertTrue(pli_flag)
