from unittest import TestCase

from aiortc.jitterbuffer import JitterBuffer


class JitterBufferTest(TestCase):
    def test_create(self):
        jbuffer = JitterBuffer(capacity=2)
        self.assertEqual(jbuffer._frames, [None, None])
        self.assertEqual(jbuffer._origin, None)

        jbuffer = JitterBuffer(capacity=4)
        self.assertEqual(jbuffer._frames, [None, None, None, None])
        self.assertEqual(jbuffer._origin, None)

    def test_add_ordered(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'0002', sequence_number=2, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'0003', sequence_number=3, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'0004', sequence_number=4, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)

        self.assertIsNotNone(jbuffer._frames[0])
        self.assertEqual(jbuffer._frames[0].payload, b'0001')
        self.assertEqual(jbuffer._frames[0].sequence_number, 1)
        self.assertIsNotNone(jbuffer._frames[1])
        self.assertEqual(jbuffer._frames[1].payload, b'0002')
        self.assertEqual(jbuffer._frames[1].sequence_number, 2)
        self.assertIsNotNone(jbuffer._frames[2])
        self.assertEqual(jbuffer._frames[2].payload, b'0003')
        self.assertEqual(jbuffer._frames[2].sequence_number, 3)
        self.assertIsNotNone(jbuffer._frames[3])
        self.assertEqual(jbuffer._frames[3].payload, b'0004')
        self.assertEqual(jbuffer._frames[3].sequence_number, 4)

    def test_add_unordered(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'0003', sequence_number=3, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'0002', sequence_number=2, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)

        self.assertIsNotNone(jbuffer._frames[0])
        self.assertEqual(jbuffer._frames[0].payload, b'0001')
        self.assertEqual(jbuffer._frames[0].sequence_number, 1)
        self.assertIsNotNone(jbuffer._frames[1])
        self.assertEqual(jbuffer._frames[1].payload, b'0002')
        self.assertEqual(jbuffer._frames[1].sequence_number, 2)
        self.assertIsNotNone(jbuffer._frames[2])
        self.assertEqual(jbuffer._frames[2].payload, b'0003')
        self.assertEqual(jbuffer._frames[2].sequence_number, 3)
        self.assertIsNone(jbuffer._frames[3])

    def test_add_seq_too_low_drop(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0002', sequence_number=2, timestamp=1234)
        self.assertEqual(jbuffer._origin, 2)
        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        self.assertEqual(jbuffer._origin, 2)

        self.assertIsNotNone(jbuffer._frames[0])
        self.assertEqual(jbuffer._frames[0].payload, b'0002')
        self.assertEqual(jbuffer._frames[0].sequence_number, 2)
        self.assertIsNone(jbuffer._frames[1])
        self.assertIsNone(jbuffer._frames[2])
        self.assertIsNone(jbuffer._frames[3])

    def test_add_seq_too_low_reset(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'2000', sequence_number=2000, timestamp=1234)
        self.assertEqual(jbuffer._origin, 2000)
        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)

        self.assertIsNotNone(jbuffer._frames[0])
        self.assertEqual(jbuffer._frames[0].payload, b'0001')
        self.assertEqual(jbuffer._frames[0].sequence_number, 1)
        self.assertIsNone(jbuffer._frames[1])
        self.assertIsNone(jbuffer._frames[2])
        self.assertIsNone(jbuffer._frames[3])

    def test_add_seq_too_high_drop(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'0005', sequence_number=5, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)

        self.assertIsNotNone(jbuffer._frames[0])
        self.assertEqual(jbuffer._frames[0].payload, b'0001')
        self.assertEqual(jbuffer._frames[0].sequence_number, 1)
        self.assertIsNone(jbuffer._frames[1])
        self.assertIsNone(jbuffer._frames[2])
        self.assertIsNone(jbuffer._frames[3])

    def test_add_seq_too_high_reset(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        self.assertEqual(jbuffer._origin, 1)
        jbuffer.add(b'3002', sequence_number=3002, timestamp=1234)
        self.assertEqual(jbuffer._origin, 3002)

        self.assertIsNotNone(jbuffer._frames[0])
        self.assertEqual(jbuffer._frames[0].payload, b'3002')
        self.assertEqual(jbuffer._frames[0].sequence_number, 3002)
        self.assertIsNone(jbuffer._frames[1])
        self.assertIsNone(jbuffer._frames[2])
        self.assertIsNone(jbuffer._frames[3])

    def test_peek(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        jbuffer.add(b'0002', sequence_number=2, timestamp=1234)
        jbuffer.add(b'0004', sequence_number=4, timestamp=1234)

        frame = jbuffer.peek(0)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.sequence_number, 1)
        self.assertEqual(repr(frame), 'JitterFrame(seq=1, ts=1234)')

        frame = jbuffer.peek(1)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.sequence_number, 2)

        frame = jbuffer.peek(2)
        self.assertIsNone(frame)

        frame = jbuffer.peek(3)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.sequence_number, 4)

        with self.assertRaises(IndexError) as cm:
            jbuffer.peek(4)
        self.assertEqual(str(cm.exception), 'Cannot peek at offset 4, capacity is 4')

    def test_remove(self):
        jbuffer = JitterBuffer(capacity=4)

        jbuffer.add(b'0001', sequence_number=1, timestamp=1234)
        jbuffer.add(b'0002', sequence_number=2, timestamp=1234)
        jbuffer.add(b'0003', sequence_number=3, timestamp=1234)
        jbuffer.add(b'0004', sequence_number=4, timestamp=1234)

        # remove 1 frame
        frames = jbuffer.remove(1)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].sequence_number, 1)

        # check buffer
        self.assertEqual(jbuffer._head, 1)
        self.assertEqual(jbuffer._origin, 2)
        self.assertIsNone(jbuffer._frames[0])
        self.assertIsNotNone(jbuffer._frames[1])
        self.assertIsNotNone(jbuffer._frames[2])
        self.assertIsNotNone(jbuffer._frames[3])

        # remove 2 frames
        frames = jbuffer.remove(2)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].sequence_number, 2)
        self.assertEqual(frames[1].sequence_number, 3)

        # check buffer
        self.assertEqual(jbuffer._head, 3)
        self.assertEqual(jbuffer._origin, 4)
        self.assertIsNone(jbuffer._frames[0])
        self.assertIsNone(jbuffer._frames[1])
        self.assertIsNone(jbuffer._frames[2])
        self.assertIsNotNone(jbuffer._frames[3])
