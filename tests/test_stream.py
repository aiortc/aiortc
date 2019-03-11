from unittest import TestCase

from aioquic.packet import QuicStreamFrame
from aioquic.stream import QuicStream


class QuicStreamTest(TestCase):
    def test_buffer_empty(self):
        stream = QuicStream()
        self.assertEqual(bytes(stream._buffer), b'')
        self.assertEqual(list(stream._received), [])
        self.assertEqual(stream._start, 0)

        # empty
        self.assertEqual(stream.pull_data(), b'')
        self.assertEqual(bytes(stream._buffer), b'')
        self.assertEqual(list(stream._received), [])
        self.assertEqual(stream._start, 0)

    def test_buffer_ordered(self):
        stream = QuicStream()

        # add data at start
        stream.add_frame(QuicStreamFrame(
            offset=0,
            data=b'01234567'))
        self.assertEqual(bytes(stream._buffer), b'01234567')
        self.assertEqual(list(stream._received), [range(0, 8)])
        self.assertEqual(stream._start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b'01234567')
        self.assertEqual(bytes(stream._buffer), b'')
        self.assertEqual(list(stream._received), [])
        self.assertEqual(stream._start, 8)

        # add more data
        stream.add_frame(QuicStreamFrame(
            offset=8,
            data=b'89012345'))
        self.assertEqual(bytes(stream._buffer), b'89012345')
        self.assertEqual(list(stream._received), [range(8, 16)])
        self.assertEqual(stream._start, 8)

        # pull data
        self.assertEqual(stream.pull_data(), b'89012345')
        self.assertEqual(bytes(stream._buffer), b'')
        self.assertEqual(list(stream._received), [])
        self.assertEqual(stream._start, 16)

    def test_buffer_ordered_2(self):
        stream = QuicStream()

        # add data at start
        stream.add_frame(QuicStreamFrame(
            offset=0,
            data=b'01234567'))
        self.assertEqual(bytes(stream._buffer), b'01234567')
        self.assertEqual(list(stream._received), [range(0, 8)])
        self.assertEqual(stream._start, 0)

        # add more data
        stream.add_frame(QuicStreamFrame(
            offset=8,
            data=b'89012345'))
        self.assertEqual(bytes(stream._buffer), b'0123456789012345')
        self.assertEqual(list(stream._received), [range(0, 16)])
        self.assertEqual(stream._start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b'0123456789012345')
        self.assertEqual(bytes(stream._buffer), b'')
        self.assertEqual(list(stream._received), [])
        self.assertEqual(stream._start, 16)

    def test_buffer_unordered(self):
        stream = QuicStream()

        # add data at offset 8
        stream.add_frame(QuicStreamFrame(
            offset=8,
            data=b'89012345'))
        self.assertEqual(bytes(stream._buffer), b'\x00\x00\x00\x00\x00\x00\x00\x0089012345')
        self.assertEqual(list(stream._received), [range(8, 16)])
        self.assertEqual(stream._start, 0)

        # add data at offset 0
        stream.add_frame(QuicStreamFrame(
            offset=0,
            data=b'01234567'))
        self.assertEqual(bytes(stream._buffer), b'0123456789012345')
        self.assertEqual(list(stream._received), [range(0, 16)])
        self.assertEqual(stream._start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b'0123456789012345')
        self.assertEqual(bytes(stream._buffer), b'')
        self.assertEqual(list(stream._received), [])
        self.assertEqual(stream._start, 16)
