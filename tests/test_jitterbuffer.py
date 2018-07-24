from unittest import TestCase

from aiortc.jitterbuffer import JitterBuffer
from aiortc.rtp import RtpPacket


class JitterBufferTest(TestCase):
    def test_create(self):
        jbuffer = JitterBuffer(capacity=2)
        self.assertEqual(jbuffer._packets, [None, None])
        self.assertEqual(jbuffer._origin, None)

        jbuffer = JitterBuffer(capacity=4)
        self.assertEqual(jbuffer._packets, [None, None, None, None])
        self.assertEqual(jbuffer._origin, None)

    def test_add_ordered(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        self.assertIsNotNone(jbuffer._packets[0])
        self.assertEqual(jbuffer._packets[0].sequence_number, 0)
        self.assertIsNotNone(jbuffer._packets[1])
        self.assertEqual(jbuffer._packets[1].sequence_number, 1)
        self.assertIsNotNone(jbuffer._packets[2])
        self.assertEqual(jbuffer._packets[2].sequence_number, 2)
        self.assertIsNotNone(jbuffer._packets[3])
        self.assertEqual(jbuffer._packets[3].sequence_number, 3)

    def test_add_unordered(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 1)

        self.assertIsNone(jbuffer._packets[0])
        self.assertIsNotNone(jbuffer._packets[1])
        self.assertEqual(jbuffer._packets[1].sequence_number, 1)
        self.assertIsNotNone(jbuffer._packets[2])
        self.assertEqual(jbuffer._packets[2].sequence_number, 2)
        self.assertIsNotNone(jbuffer._packets[3])
        self.assertEqual(jbuffer._packets[3].sequence_number, 3)

    def test_add_seq_too_low_drop(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        self.assertEqual(jbuffer._origin, 2)
        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 2)

        self.assertIsNone(jbuffer._packets[0])
        self.assertIsNone(jbuffer._packets[1])
        self.assertIsNotNone(jbuffer._packets[2])
        self.assertEqual(jbuffer._packets[2].sequence_number, 2)
        self.assertIsNone(jbuffer._packets[3])

    def test_add_seq_too_low_reset(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=2000, timestamp=1234))
        self.assertEqual(jbuffer._origin, 2000)
        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        self.assertEqual(jbuffer._origin, 1)

        self.assertIsNone(jbuffer._packets[0])
        self.assertIsNotNone(jbuffer._packets[1])
        self.assertEqual(jbuffer._packets[1].sequence_number, 1)
        self.assertIsNone(jbuffer._packets[2])
        self.assertIsNone(jbuffer._packets[3])

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

        self.assertIsNotNone(jbuffer._packets[0])
        self.assertEqual(jbuffer._packets[0].sequence_number, 4)
        self.assertIsNotNone(jbuffer._packets[1])
        self.assertEqual(jbuffer._packets[1].sequence_number, 1)
        self.assertIsNotNone(jbuffer._packets[2])
        self.assertEqual(jbuffer._packets[2].sequence_number, 2)
        self.assertIsNotNone(jbuffer._packets[3])
        self.assertEqual(jbuffer._packets[3].sequence_number, 3)

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

        self.assertIsNone(jbuffer._packets[0])
        self.assertIsNone(jbuffer._packets[1])
        self.assertIsNone(jbuffer._packets[2])
        self.assertIsNotNone(jbuffer._packets[3])
        self.assertEqual(jbuffer._packets[3].sequence_number, 7)

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

        self.assertIsNotNone(jbuffer._packets[0])
        self.assertEqual(jbuffer._packets[0].sequence_number, 8)
        self.assertIsNone(jbuffer._packets[1])
        self.assertIsNone(jbuffer._packets[2])
        self.assertIsNone(jbuffer._packets[3])

    def test_add_seq_too_high_reset(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)
        jbuffer.add(RtpPacket(sequence_number=3000, timestamp=1234))
        self.assertEqual(jbuffer._origin, 3000)

        self.assertIsNotNone(jbuffer._packets[0])
        self.assertEqual(jbuffer._packets[0].sequence_number, 3000)
        self.assertIsNone(jbuffer._packets[1])
        self.assertIsNone(jbuffer._packets[2])
        self.assertIsNone(jbuffer._packets[3])

    def test_remove(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(RtpPacket(sequence_number=0, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=1, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=2, timestamp=1234))
        jbuffer.add(RtpPacket(sequence_number=3, timestamp=1234))
        self.assertEqual(jbuffer._origin, 0)

        # remove 1 packet
        jbuffer.remove(1)
        self.assertEqual(jbuffer._origin, 1)
        self.assertIsNone(jbuffer._packets[0])
        self.assertIsNotNone(jbuffer._packets[1])
        self.assertIsNotNone(jbuffer._packets[2])
        self.assertIsNotNone(jbuffer._packets[3])

        # remove 2 packets
        jbuffer.remove(2)
        self.assertEqual(jbuffer._origin, 3)
        self.assertIsNone(jbuffer._packets[0])
        self.assertIsNone(jbuffer._packets[1])
        self.assertIsNone(jbuffer._packets[2])
        self.assertIsNotNone(jbuffer._packets[3])

    def test_remove_frame(self):
        jbuffer = JitterBuffer(capacity=4)

        packet = RtpPacket(sequence_number=0, timestamp=1234)
        packet._data = b'0000'
        jbuffer.add(packet)
        self.assertIsNone(jbuffer.remove_frame())

        packet = RtpPacket(sequence_number=1, timestamp=1234)
        packet._data = b'0001'
        jbuffer.add(packet)
        self.assertIsNone(jbuffer.remove_frame())

        packet = RtpPacket(sequence_number=2, timestamp=1234)
        packet._data = b'0002'
        jbuffer.add(packet)
        self.assertIsNone(jbuffer.remove_frame())

        packet = RtpPacket(sequence_number=3, timestamp=1235)
        packet._data = b'0003'
        jbuffer.add(packet)
        frame = jbuffer.remove_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.data, b'000000010002')
        self.assertEqual(frame.timestamp, 1234)
