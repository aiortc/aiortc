import asyncio
from unittest import TestCase

from aioquic.packet import QuicStreamFrame
from aioquic.packet_builder import QuicDeliveryState
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
        self.assertEqual(stream._recv_buffer_start, 0)

        # empty
        self.assertEqual(stream.pull_data(), b"")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 0)

    def test_recv_ordered(self):
        stream = QuicStream()

        # add data at start
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"01234567")
        self.assertEqual(list(stream._recv_ranges), [range(0, 8)])
        self.assertEqual(stream._recv_buffer_start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b"01234567")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 8)

        # add more data
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345"))
        self.assertEqual(bytes(stream._recv_buffer), b"89012345")
        self.assertEqual(list(stream._recv_ranges), [range(8, 16)])
        self.assertEqual(stream._recv_buffer_start, 8)

        # pull data
        self.assertEqual(stream.pull_data(), b"89012345")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 16)

    def test_recv_ordered_2(self):
        stream = QuicStream()

        # add data at start
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"01234567")
        self.assertEqual(list(stream._recv_ranges), [range(0, 8)])
        self.assertEqual(stream._recv_buffer_start, 0)

        # add more data
        stream.add_frame(QuicStreamFrame(offset=8, data=b"89012345"))
        self.assertEqual(bytes(stream._recv_buffer), b"0123456789012345")
        self.assertEqual(list(stream._recv_ranges), [range(0, 16)])
        self.assertEqual(stream._recv_buffer_start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b"0123456789012345")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 16)

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
        self.assertEqual(stream._recv_buffer_start, 0)

        # add data at offset 0
        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"0123456789012345")
        self.assertEqual(list(stream._recv_ranges), [range(0, 16)])
        self.assertEqual(stream._recv_buffer_start, 0)

        # pull data
        self.assertEqual(stream.pull_data(), b"0123456789012345")
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 16)

    def test_recv_offset_only(self):
        stream = QuicStream()

        # add data at offset 0
        stream.add_frame(QuicStreamFrame(offset=0, data=b""))
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 0)

        # add data at offset 8
        stream.add_frame(QuicStreamFrame(offset=8, data=b""))
        self.assertEqual(
            bytes(stream._recv_buffer), b"\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 0)

    def test_recv_already_fully_consumed(self):
        stream = QuicStream()

        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(stream.pull_data(), b"01234567")

        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(bytes(stream._recv_buffer), b"")
        self.assertEqual(list(stream._recv_ranges), [])
        self.assertEqual(stream._recv_buffer_start, 8)

        self.assertEqual(stream.pull_data(), b"")
        self.assertEqual(stream._recv_buffer_start, 8)

    def test_recv_already_partially_consumed(self):
        stream = QuicStream()

        stream.add_frame(QuicStreamFrame(offset=0, data=b"01234567"))
        self.assertEqual(stream.pull_data(), b"01234567")

        stream.add_frame(QuicStreamFrame(offset=0, data=b"0123456789012345"))
        self.assertEqual(bytes(stream._recv_buffer), b"89012345")
        self.assertEqual(list(stream._recv_ranges), [range(8, 16)])
        self.assertEqual(stream._recv_buffer_start, 8)

        self.assertEqual(stream.pull_data(), b"89012345")
        self.assertEqual(stream._recv_buffer_start, 16)

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

    def test_send_data(self):
        stream = QuicStream()
        self.assertTrue(stream.can_write_eof())

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write data
        stream.write(b"0123456789012345")
        self.assertEqual(stream.get_write_buffer_size(), 16)
        self.assertEqual(list(stream._send_pending), [range(0, 16)])

        # send a chunk
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"01234567")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 0)
        self.assertEqual(stream.get_write_buffer_size(), 16)
        self.assertEqual(list(stream._send_pending), [range(8, 16)])

        # send another chunk
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"89012345")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 8)
        self.assertEqual(stream.get_write_buffer_size(), 16)
        self.assertEqual(list(stream._send_pending), [])

        # nothing more to send
        frame = stream.get_frame(8)
        self.assertIsNone(frame)
        self.assertEqual(stream.get_write_buffer_size(), 16)
        self.assertEqual(list(stream._send_pending), [])

        # first chunk gets acknowledged
        stream.on_data_delivery(QuicDeliveryState.ACKED, 0, 8)
        self.assertEqual(stream.get_write_buffer_size(), 8)

        # second chunk gets acknowledged
        stream.on_data_delivery(QuicDeliveryState.ACKED, 8, 16)
        self.assertEqual(stream.get_write_buffer_size(), 0)

    def test_send_data_and_fin(self):
        stream = QuicStream()

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write data and EOF, send a chunk
        stream.write(b"0123456789012345")
        stream.write_eof()
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"01234567")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 0)

        # send another chunk
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"89012345")
        self.assertTrue(frame.fin)
        self.assertEqual(frame.offset, 8)

        # nothing more to send
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

    def test_send_data_lost(self):
        stream = QuicStream()

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write data and EOF
        stream.write(b"0123456789012345")
        stream.write_eof()
        self.assertEqual(list(stream._send_pending), [range(0, 16)])

        # send a chunk
        self.assertEqual(
            stream.get_frame(8), QuicStreamFrame(data=b"01234567", fin=False, offset=0)
        )
        self.assertEqual(list(stream._send_pending), [range(8, 16)])

        # send another chunk
        self.assertEqual(
            stream.get_frame(8), QuicStreamFrame(data=b"89012345", fin=True, offset=8)
        )
        self.assertEqual(list(stream._send_pending), [])

        # nothing more to send
        self.assertIsNone(stream.get_frame(8))
        self.assertEqual(list(stream._send_pending), [])

        # a chunk gets lost
        stream.on_data_delivery(QuicDeliveryState.LOST, 0, 8)
        self.assertEqual(list(stream._send_pending), [range(0, 8)])

        # send chunk again
        self.assertEqual(
            stream.get_frame(8), QuicStreamFrame(data=b"01234567", fin=False, offset=0)
        )
        self.assertEqual(list(stream._send_pending), [])

    def test_send_data_lost_fin(self):
        stream = QuicStream()

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write data and EOF
        stream.write(b"0123456789012345")
        stream.write_eof()
        self.assertEqual(list(stream._send_pending), [range(0, 16)])

        # send a chunk
        self.assertEqual(
            stream.get_frame(8), QuicStreamFrame(data=b"01234567", fin=False, offset=0)
        )
        self.assertEqual(list(stream._send_pending), [range(8, 16)])

        # send another chunk
        self.assertEqual(
            stream.get_frame(8), QuicStreamFrame(data=b"89012345", fin=True, offset=8)
        )
        self.assertEqual(list(stream._send_pending), [])

        # nothing more to send
        self.assertIsNone(stream.get_frame(8))
        self.assertEqual(list(stream._send_pending), [])

        # a chunk gets lost
        stream.on_data_delivery(QuicDeliveryState.LOST, 8, 16)
        self.assertEqual(list(stream._send_pending), [range(8, 16)])

        # send chunk again
        self.assertEqual(
            stream.get_frame(8), QuicStreamFrame(data=b"89012345", fin=True, offset=8)
        )
        self.assertEqual(list(stream._send_pending), [])

    def test_send_blocked(self):
        stream = QuicStream()
        max_offset = 12

        # nothing to send yet
        frame = stream.get_frame(8, max_offset)
        self.assertIsNone(frame)
        self.assertEqual(list(stream._send_pending), [])

        # write data, send a chunk
        stream.write(b"0123456789012345")
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"01234567")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 0)
        self.assertEqual(list(stream._send_pending), [range(8, 16)])

        # send is limited by peer
        frame = stream.get_frame(8, max_offset)
        self.assertEqual(frame.data, b"8901")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 8)
        self.assertEqual(list(stream._send_pending), [range(12, 16)])

        # unable to send, blocked
        frame = stream.get_frame(8, max_offset)
        self.assertIsNone(frame)
        self.assertEqual(list(stream._send_pending), [range(12, 16)])

        # write more data, still blocked
        stream.write(b"abcdefgh")
        frame = stream.get_frame(8, max_offset)
        self.assertIsNone(frame)
        self.assertEqual(list(stream._send_pending), [range(12, 24)])

        # peer raises limit, send some data
        max_offset += 8
        frame = stream.get_frame(8, max_offset)
        self.assertEqual(frame.data, b"2345abcd")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 12)
        self.assertEqual(list(stream._send_pending), [range(20, 24)])

        # peer raises limit again, send remaining data
        max_offset += 8
        frame = stream.get_frame(8, max_offset)
        self.assertEqual(frame.data, b"efgh")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 20)
        self.assertEqual(list(stream._send_pending), [])

        # nothing more to send
        frame = stream.get_frame(8, max_offset)
        self.assertIsNone(frame)

    def test_send_fin_only(self):
        stream = QuicStream()

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write EOF
        stream.write_eof()
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"")
        self.assertTrue(frame.fin)
        self.assertEqual(frame.offset, 0)

        # nothing more to send
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

    def test_send_fin_only_despite_blocked(self):
        stream = QuicStream()

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write EOF
        stream.write_eof()
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"")
        self.assertTrue(frame.fin)
        self.assertEqual(frame.offset, 0)

        # nothing more to send
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

    def test_send_data_using_writelines(self):
        stream = QuicStream()

        # nothing to send yet
        frame = stream.get_frame(8)
        self.assertIsNone(frame)

        # write data, send a chunk
        stream.writelines([b"01234567", b"89012345"])
        self.assertEqual(list(stream._send_pending), [range(0, 16)])
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"01234567")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 0)
        self.assertEqual(list(stream._send_pending), [range(8, 16)])

        # send another chunk
        frame = stream.get_frame(8)
        self.assertEqual(frame.data, b"89012345")
        self.assertFalse(frame.fin)
        self.assertEqual(frame.offset, 8)
        self.assertEqual(list(stream._send_pending), [])

        # nothing more to send
        frame = stream.get_frame(8)
        self.assertIsNone(frame)
        self.assertEqual(list(stream._send_pending), [])
