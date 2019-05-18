import asyncio
from unittest import TestCase

from aioquic.packet import QuicStreamFrame
from aioquic.stream import QuicStream

from .utils import run


async def delay(coro):
    await asyncio.sleep(0.1)
    await coro()


class QuicStreamTest(TestCase):
    def test_recv_empty(self):
        stream = QuicStream()
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 0)

        # empty
        self.assertEqual(stream.pull_data(), b"")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 0)

    def test_recv_ordered(self):
        stream = QuicStream()

        # add data at start
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"01234567")
        self.assertEqual(list(stream._recv_ranges), [range(0, 8)])
        self.assertEqual(stream._recv_start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b"01234567")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 8)

        # add more data
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345"))
        self.assertEqual(bytes(stream._recv_buffer), b"89012345")
        self.assertEqual(list(stream._recv_ranges), [range(8, 16)])
        self.assertEqual(stream._recv_start, 8)

        # pull data
        self.assertEqual(stream.pull_data(), b"89012345")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 16)

    def test_recv_ordered_2(self):
        stream = QuicStream()

        # add data at start
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"01234567")
        self.assertEqual(list(stream._recv_ranges), [range(0, 8)])
        self.assertEqual(stream._recv_start, 0)

        # add more data
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345"))
        self.assertEqual(bytes(stream._recv_buffer), b"0123456789012345")
        self.assertEqual(list(stream._recv_ranges), [range(0, 16)])
        self.assertEqual(stream._recv_start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b"0123456789012345")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 16)

    def test_recv_ordered_3(self):
        stream = QuicStream(stream_id=0)

        async def add_frame():
            stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))

        data, _ = run(asyncio.gather(stream.reader.read(1024), delay(add_frame)))
        self.assertEqual(data, b"01234567")

    def test_recv_unordered(self):
        stream = QuicStream()

        # add data at offset 8
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345"))
        self.assertEqual(
            bytes(stream._recv_buffer), b"\x00\x00\x00\x00\x00\x00\x00\x0089012345"
        )
        self.assertEqual(list(stream._recv_ranges), [range(8, 16)])
        self.assertEqual(stream._recv_start, 0)

        # add data at offset 0
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"0123456789012345")
        self.assertEqual(list(stream._recv_ranges), [range(0, 16)])
        self.assertEqual(stream._recv_start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b"0123456789012345")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 16)

    def test_recv_offset_only(self):
        stream = QuicStream()

        # add data at offset 0
        stream.add_frame(QuicStreamFrame(offset=0, data=b""))
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 0)

        # add data at offset 8
        stream.add_frame(QuicStreamFrame(offset=8, data=b""))
        self.assertEqual(
            bytes(stream._recv_buffer), b"\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 0)

    def test_recv_already_fully_consumed(self):
        stream = QuicStream()

        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(stream.pull_data(), b"01234567")

        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_start, 8)

        self.assertEqual(stream.pull_data(), b"")
        self.assertEqual(stream._recv_start, 8)

    def test_recv_already_partially_consumed(self):
        stream = QuicStream()

        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(stream.pull_data(), b"01234567")

        stream.add_frame(QuicStreamFrame(offset=0, data=b"0123456789012345"))
        self.assertEqual(bytes(stream._recv_buffer), b"89012345")
        self.assertEqual(list(stream._recv_ranges), [range(8, 16)])
        self.assertEqual(stream._recv_start, 8)

        self.assertEqual(stream.pull_data(), b"89012345")
        self.assertEqual(stream._recv_start, 16)

    def test_recv_fin(self):
        stream = QuicStream(stream_id=0)
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345", fin=True))

        self.assertEqual(run(stream.reader.read()), b"0123456789012345")

    def test_recv_fin_out_of_order(self):
        stream = QuicStream(stream_id=0)
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345", fin=True))
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))

        self.assertEqual(run(stream.reader.read()), b"0123456789012345")

    def test_recv_fin_then_data(self):
        stream = QuicStream(stream_id=0)
        stream.add_frame(QuicStreamFrame(offset=0, data=b"", fin=True))
        with self.assertRaises(Exception) as cm:
            stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(str(cm.exception), "Data received beyond FIN")

    def test_recv_fin_twice(self):
        stream = QuicStream(stream_id=0)
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345", fin=True))
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345", fin=True))

        self.assertEqual(run(stream.reader.read()), b"0123456789012345")

    def test_recv_fin_without_data(self):
        stream = QuicStream(stream_id=0)
        stream.add_frame(QuicStreamFrame(offset=0, data=b"", fin=True))

        self.assertEqual(run(stream.reader.read()), b"")
