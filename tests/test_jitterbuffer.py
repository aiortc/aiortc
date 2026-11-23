from typing import Optional
from unittest import TestCase

from aiortc.jitterbuffer import JitterBuffer, JitterFrame
from aiortc.rtp import RtpPacket


class JitterBufferTest(TestCase):
    def assertFrames(
        self, frames: list[JitterFrame], expected: list[tuple[bytes, int]]
    ) -> None:
        self.assertEqual([(x.data, x.timestamp) for x in frames], expected)

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

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [0, None, None, None])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [0, 1, None, None])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [0, 1, 2, None])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [0, 1, 2, 3])
        self.assertEqual(jbuffer._origin, 0)
        self.assertFalse(pli_flag)

    def test_add_unordered(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [None, 1, None, None])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [None, 1, None, 3])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [None, 1, 2, 3])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_drop(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [None, None, 2, None])
        self.assertEqual(jbuffer._origin, 2)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [None, None, 2, None])
        self.assertEqual(jbuffer._origin, 2)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_reset(self) -> None:
        jbuffer = JitterBuffer(capacity=4)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [2000, None, None, None])
        self.assertEqual(jbuffer._origin, 2000)
        self.assertFalse(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertPackets(jbuffer, [None, 1, None, None])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_reset_prefetch(self) -> None:
        """
        Audio jitter buffer, a reset releases the frames kept back.
        """
        jbuffer = JitterBuffer(capacity=16, prefetch=4)
        for sequence_number in range(2000, 2005):
            packet = RtpPacket(
                sequence_number=sequence_number, timestamp=sequence_number
            )
            packet._data = b"%04d" % sequence_number
            pli_flag, frames = jbuffer.add(packet)
        self.assertEqual(jbuffer._origin, 2001)

        packet = RtpPacket(sequence_number=1, timestamp=1)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"2001", 2001), (b"2002", 2002), (b"2003", 2003)])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

    def test_add_seq_too_low_retransmitted(self) -> None:
        """
        Video jitter buffer, late retransmissions far behind are dropped.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)
        for sequence_number in range(150):
            packet = RtpPacket(
                sequence_number=sequence_number, timestamp=sequence_number, marker=1
            )
            packet._data = b"%04d" % sequence_number
            jbuffer.add(packet)
        self.assertEqual(jbuffer._origin, 150)

        # Sequential retransmissions do not show the sender restarted.
        for sequence_number in [10, 11]:
            packet = RtpPacket(
                sequence_number=sequence_number, timestamp=sequence_number, marker=1
            )
            packet._data = b"%04d" % sequence_number
            pli_flag, frames = jbuffer.add(packet, retransmitted=True)
            self.assertFrames(frames, [])
            self.assertEqual(jbuffer._origin, 150)
            self.assertFalse(pli_flag)

        packet = RtpPacket(sequence_number=150, timestamp=150, marker=1)
        packet._data = b"0150"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0150", 150)])
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
        packet._data = b"0000"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=1, timestamp=1235)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=2, timestamp=1236)
        packet._data = b"0002"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=3, timestamp=1237)
        packet._data = b"0003"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=4, timestamp=1238)
        packet._data = b"0003"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0000", 1234)])

        packet = RtpPacket(sequence_number=5, timestamp=1239)
        packet._data = b"0004"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0001", 1235)])

    def test_remove_audio_frame_marker(self) -> None:
        """
        Audio jitter buffer, the marker bit starts a talkspurt.
        """
        jbuffer = JitterBuffer(capacity=16)

        packet = RtpPacket(sequence_number=0, timestamp=1234, marker=1)
        packet._data = b"0000"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=1, timestamp=1235)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0000", 1234)])

    def test_remove_audio_frames(self) -> None:
        """
        Audio jitter buffer, a late packet completes several frames.
        """
        jbuffer = JitterBuffer(capacity=16, prefetch=4)

        for sequence_number in [0, 2, 3, 4, 5, 6]:
            packet = RtpPacket(
                sequence_number=sequence_number, timestamp=1234 + sequence_number
            )
            packet._data = b"%04d" % sequence_number
            pli_flag, frames = jbuffer.add(packet)
            self.assertFrames(frames, [])

        # All but the last 3 complete frames leave the buffer.
        packet = RtpPacket(sequence_number=1, timestamp=1235)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0000", 1234), (b"0001", 1235), (b"0002", 1236)])
        self.assertEqual(jbuffer._origin, 3)

    def test_remove_audio_frame_overflow(self) -> None:
        """
        Audio jitter buffer, an overflow releases the frames kept back.
        """
        jbuffer = JitterBuffer(capacity=16, prefetch=4)

        for sequence_number in range(18):
            # Packet 6 is lost.
            if sequence_number == 6:
                continue
            packet = RtpPacket(
                sequence_number=sequence_number, timestamp=sequence_number
            )
            packet._data = b"%04d" % sequence_number
            pli_flag, frames = jbuffer.add(packet)
        self.assertEqual(jbuffer._origin, 2)

        # The complete frames leave the buffer to make room.
        packet = RtpPacket(sequence_number=18, timestamp=18)
        packet._data = b"0018"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0002", 2), (b"0003", 3), (b"0004", 4)])
        self.assertEqual(jbuffer._origin, 5)
        self.assertFalse(pli_flag)

    def test_remove_video_frame(self) -> None:
        """
        Video jitter buffer.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"0000"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=1, timestamp=1234)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=2, timestamp=1234)
        packet._data = b"0002"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=3, timestamp=1235)
        packet._data = b"0003"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"000000010002", 1234)])

    def test_remove_video_frame_marker(self) -> None:
        """
        Video jitter buffer, the marker bit ends a frame.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"0000"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=1, timestamp=1234, marker=1)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"00000001", 1234)])
        self.assertEqual(jbuffer._origin, 2)

        packet = RtpPacket(sequence_number=2, timestamp=1235, marker=1)
        packet._data = b"0002"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0002", 1235)])
        self.assertEqual(jbuffer._origin, 3)

    def test_remove_video_frame_marker_missing(self) -> None:
        """
        Video jitter buffer, the marker bit ends a frame only without a gap.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b"0000"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=2, timestamp=1234, marker=1)
        packet._data = b"0002"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [])

        packet = RtpPacket(sequence_number=1, timestamp=1234)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"000000010002", 1234)])

    def test_remove_video_frames(self) -> None:
        """
        Video jitter buffer, a late packet completes several frames.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        packet = RtpPacket(sequence_number=0, timestamp=1234, marker=1)
        packet._data = b"0000"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0000", 1234)])

        for sequence_number in [2, 3]:
            packet = RtpPacket(
                sequence_number=sequence_number,
                timestamp=1234 + sequence_number,
                marker=1,
            )
            packet._data = b"%04d" % sequence_number
            pli_flag, frames = jbuffer.add(packet)
            self.assertFrames(frames, [])

        # All the complete frames leave the buffer.
        packet = RtpPacket(sequence_number=1, timestamp=1235, marker=1)
        packet._data = b"0001"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0001", 1235), (b"0002", 1236), (b"0003", 1237)])
        self.assertEqual(jbuffer._origin, 4)

    def test_remove_video_frame_overdue(self) -> None:
        """
        Video jitter buffer, a missing packet is given up on after a second.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        # The first frame is packets 0 to 2, and packet 1 is lost.
        packets = [
            RtpPacket(sequence_number=0, timestamp=0),
            RtpPacket(sequence_number=2, timestamp=0, marker=1),
        ]

        # The next frames are complete, with one packet each.
        for sequence_number in range(3, 33):
            packets.append(
                RtpPacket(
                    sequence_number=sequence_number,
                    timestamp=(sequence_number - 2) * 3000,
                    marker=1,
                )
            )

        for packet in packets:
            packet._data = b"%04d" % packet.sequence_number
            pli_flag, frames = jbuffer.add(packet)
            self.assertFrames(frames, [])
            self.assertFalse(pli_flag)

        # One second after the first frame, it is dropped.
        packet = RtpPacket(sequence_number=33, timestamp=93000, marker=1)
        packet._data = b"0033"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"%04d" % i, (i - 2) * 3000) for i in range(3, 34)])
        self.assertEqual(jbuffer._origin, 34)
        self.assertTrue(pli_flag)

    def test_remove_video_frames_overdue(self) -> None:
        """
        Video jitter buffer, several missing packets are given up on at once.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        # Packet 1 of the first frame and packet 4 of the second frame are lost.
        packets = [
            RtpPacket(sequence_number=0, timestamp=0),
            RtpPacket(sequence_number=2, timestamp=0, marker=1),
            RtpPacket(sequence_number=3, timestamp=3000),
            RtpPacket(sequence_number=5, timestamp=3000, marker=1),
        ]
        for packet in packets:
            packet._data = b"%04d" % packet.sequence_number
            pli_flag, frames = jbuffer.add(packet)
            self.assertFrames(frames, [])
            self.assertFalse(pli_flag)

        # After a pause, both incomplete frames are dropped.
        packet = RtpPacket(sequence_number=6, timestamp=120000, marker=1)
        packet._data = b"0006"
        pli_flag, frames = jbuffer.add(packet)
        self.assertFrames(frames, [(b"0006", 120000)])
        self.assertEqual(jbuffer._origin, 7)
        self.assertTrue(pli_flag)

    def test_pli_flag(self) -> None:
        """
        Video jitter buffer.
        """
        jbuffer = JitterBuffer(capacity=128, is_video=True)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertEqual(jbuffer._origin, 2000)
        self.assertFalse(pli_flag)

        # test_add_seq_too_low_reset for video (capacity >= 128)
        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertFrames(frames, [])
        self.assertEqual(jbuffer._origin, 1)
        self.assertTrue(pli_flag)

        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=128, timestamp=1235))
        self.assertFrames(frames, [])
        self.assertEqual(jbuffer._origin, 1)
        self.assertFalse(pli_flag)

        # test_add_seq_too_high_discard_one for video (capacity >= 128)
        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=129, timestamp=1235))
        self.assertFrames(frames, [])
        self.assertEqual(jbuffer._origin, 128)
        self.assertTrue(pli_flag)

        # test_add_seq_too_high_reset for video (capacity >= 128)
        pli_flag, frames = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=2345))
        self.assertFrames(frames, [])
        self.assertEqual(jbuffer._origin, 2000)
        self.assertTrue(pli_flag)
