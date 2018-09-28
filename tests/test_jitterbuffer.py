from unittest import TestCase

from aiortc.jitterbuffer import JitterBuffer
from aiortc.rtp import RtpPacket


class JitterBufferTest(TestCase):
    def assertPackets(self, jbuffer, expected):
        found = [x.sequence_number if x else None for x in jbuffer._packets]
        self.assertEqual(found, expected)

    def test_create(self):
        jbuffer = JitterBuffer(capacity=2)
        self.assertEqual(jbuffer._packets, [None, None])
        self.assertEqual(jbuffer._origin, None)

        jbuffer = JitterBuffer(capacity=4)
        self.assertEqual(jbuffer._packets, [None, None, None, None])
        self.assertEqual(jbuffer._origin, None)

    def test_add_ordered(self):
        jbuffer = JitterBuffer(capacity=4)

        frame = jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, None, None, None])
        self.assertEqual(jbuffer._origin, 0)

        frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, 1, None, None])
        self.assertEqual(jbuffer._origin, 0)

        frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, 1, 2, None])
        self.assertEqual(jbuffer._origin, 0)

        frame = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [0, 1, 2, 3])
        self.assertEqual(jbuffer._origin, 0)

    def test_add_unordered(self):
        jbuffer = JitterBuffer(capacity=4)

        frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, None, None])
        self.assertEqual(jbuffer._origin, 1)

        frame = jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, None, 3])
        self.assertEqual(jbuffer._origin, 1)

        frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, 2, 3])
        self.assertEqual(jbuffer._origin, 1)

    def test_add_seq_too_low_drop(self):
        jbuffer = JitterBuffer(capacity=4)

        frame = jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, None, 2, None])
        self.assertEqual(jbuffer._origin, 2)

        frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, None, 2, None])
        self.assertEqual(jbuffer._origin, 2)

    def test_add_seq_too_low_reset(self):
        jbuffer = JitterBuffer(capacity=4)

        frame = jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [2000, None, None, None])
        self.assertEqual(jbuffer._origin, 2000)

        frame = jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertIsNone(frame)
        self.assertPackets(jbuffer, [None, 1, None, None])
        self.assertEqual(jbuffer._origin, 1)

    def test_add_seq_too_high_discard_one(self):
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
        self.assertEqual(jbuffer._origin, 1)

        self.assertPackets(jbuffer, [4, 1, 2, 3])

    def test_add_seq_too_high_discard_four(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        jbuffer.add(RtpPacket(sequence_number=7, timestamp=1234))
        self.assertEqual(jbuffer._origin, 4)

        self.assertPackets(jbuffer, [None, None, None, 7])

    def test_add_seq_too_high_discard_more(self):
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

    def test_add_seq_too_high_reset(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        self.assertPackets(jbuffer, [0, None, None, None])

        jbuffer.add(RtpPacket(sequence_number=3000, timestamp=1234))
        self.assertEqual(jbuffer._origin, 3000)
        self.assertPackets(jbuffer, [3000, None, None, None])

    def test_remove(self):
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

    def test_remove_audio_frame(self):
        """
        Audio jitter buffer.
        """
        jbuffer = JitterBuffer(capacity=16, prefetch=4)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b'0000'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=1, timestamp=1235)
        packet._data = b'0001'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=2, timestamp=1236)
        packet._data = b'0002'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=3, timestamp=1237)
        packet._data = b'0003'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=4, timestamp=1238)
        packet._data = b'0003'
        frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b'0000')
        self.assertEqual(frame.timestamp, 1234)

        packet = RtpPacket(sequence_number=5, timestamp=1239)
        packet._data = b'0004'
        frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b'0001')
        self.assertEqual(frame.timestamp, 1235)

    def test_remove_video_frame(self):
        """
        Video jitter buffer.
        """
        jbuffer = JitterBuffer(capacity=128)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b'0000'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=1, timestamp=1234)
        packet._data = b'0001'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=2, timestamp=1234)
        packet._data = b'0002'
        frame = jbuffer.add(packet)
        self.assertIsNone(frame)

        packet = RtpPacket(sequence_number=3, timestamp=1235)
        packet._data = b'0003'
        frame = jbuffer.add(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b'000000010002')
        self.assertEqual(frame.timestamp, 1234)
