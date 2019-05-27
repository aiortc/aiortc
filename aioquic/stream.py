import asyncio
from typing import Any, Optional

from .packet import QuicDeliveryState, QuicStreamFrame
from .rangeset import RangeSet


class QuicStream(asyncio.BaseTransport):
    def __init__(
        self,
        stream_id: Optional[int] = None,
        connection: Optional[Any] = None,
        max_stream_data_local: int = 0,
        max_stream_data_remote: int = 0,
    ) -> None:
        self._connection = connection
        self.max_stream_data_local = max_stream_data_local
        self.max_stream_data_remote = max_stream_data_remote

        if stream_id is not None:
            self.reader = asyncio.StreamReader()
            self.writer = asyncio.StreamWriter(self, None, self.reader, None)
        else:
            self.reader = None
            self.writer = None

        self._recv_buffer = bytearray()
        self._recv_fin = False
        self._recv_highest = 0  # the highest offset ever seen
        self._recv_start = 0  # the offset for the start of the buffer
        self._recv_ranges = RangeSet()

        self._send_acked = RangeSet()
        self._send_buffer = bytearray()
        self._send_buffer_fin: Optional[int] = None
        self._send_highest = 0
        self._send_pending = RangeSet()
        self._send_pending_eof = False
        self._send_buffer_start = 0  # the offset for the start of the buffer
        self._send_buffer_stop = 0  # the offset for the stop of the buffer

        self.__stream_id = stream_id

    @property
    def stream_id(self) -> Optional[int]:
        return self.__stream_id

    def connection_lost(self, exc: Exception) -> None:
        if self.reader is not None:
            if exc is None:
                self.reader.feed_eof()
            else:
                self.reader.set_exception(exc)

    # reader

    def add_frame(self, frame: QuicStreamFrame) -> None:
        """
        Add a frame of received data.
        """
        pos = frame.offset - self._recv_start
        count = len(frame.data)
        frame_end = frame.offset + count

        # we should receive no more data beyond FIN!
        if self._recv_fin and frame_end > self._recv_highest:
            raise Exception("Data received beyond FIN")

        if pos + count > 0:
            # frame has been partially consumed
            if pos < 0:
                count += pos
                frame.data = frame.data[-pos:]
                frame.offset -= pos
                pos = 0

            # marked received
            if count:
                self._recv_ranges.add(frame.offset, frame_end)
            if frame_end > self._recv_highest:
                self._recv_highest = frame_end

            # add data
            gap = pos - len(self._recv_buffer)
            if gap > 0:
                self._recv_buffer += bytearray(gap)
            self._recv_buffer[pos : pos + count] = frame.data

        if frame.fin:
            self._recv_fin = True

        if self.reader:
            if self.has_data_to_read():
                self.reader.feed_data(self.pull_data())
            if self._recv_fin and not self._recv_ranges:
                self.reader.feed_eof()

    def has_data_to_read(self) -> bool:
        return (
            bool(self._recv_ranges) and self._recv_ranges[0].start == self._recv_start
        )

    def pull_data(self) -> bytes:
        """
        Pull received data.
        """
        if not self.has_data_to_read():
            return b""

        r = self._recv_ranges.shift()
        pos = r.stop - r.start
        data = self._recv_buffer[:pos]
        self._recv_buffer = self._recv_buffer[pos:]
        self._recv_start = r.stop
        return data

    # writer

    @property
    def next_send_offset(self) -> int:
        """
        The offset for the next frame to send.

        This is used to determine the space needed for the frame's `offset` field.
        """
        try:
            return self._send_pending[0].start
        except IndexError:
            return self._send_buffer_stop

    def get_frame(self, size: int) -> Optional[QuicStreamFrame]:
        """
        Get a frame of data to send.
        """
        # check there is something to send
        if not (self._send_pending or self._send_pending_eof):
            return None

        # FIN only
        if not self._send_pending:
            self._send_pending_eof = False
            return QuicStreamFrame(fin=True, offset=self._send_buffer_stop)

        # apply flow control
        r = self._send_pending[0]
        size = min(size, r.stop - r.start)
        if self.stream_id is not None:
            size = min(size, self.max_stream_data_remote - r.start)
        if size <= 0:
            return None

        # create frame
        send = range(r.start, r.start + size)
        frame = QuicStreamFrame(
            data=self._send_buffer[
                send.start
                - self._send_buffer_start : send.start
                + size
                - self._send_buffer_start
            ],
            offset=send.start,
        )
        self._send_pending.subtract(send.start, send.stop)

        # track the highest offset ever sent
        if send.stop > self._send_highest:
            self._send_highest = send.stop

        # if the buffer is empty and EOF was written, set the FIN bit
        if self._send_buffer_fin == send.stop:
            frame.fin = True
            self._send_pending_eof = False

        return frame

    def is_blocked(self) -> bool:
        """
        Returns True if there is data to send but the peer's MAX_STREAM_DATA
        prevents us from sending it.
        """
        return (
            bool(self._send_pending)
            and self._send_pending[0].start >= self.max_stream_data_remote
        )

    def on_data_delivery(
        self, delivery: QuicDeliveryState, start: int, stop: int
    ) -> None:
        """
        Callback when sent data is ACK'd.
        """
        if stop - start:
            if delivery == QuicDeliveryState.ACKED:
                self._send_acked.add(start, stop)
                first_range = self._send_acked[0]
                if first_range.start == self._send_buffer_start:
                    size = first_range.stop - first_range.start
                    self._send_acked.shift()
                    self._send_buffer_start += size
                    del self._send_buffer[:size]
            else:
                self._send_pending.add(start, stop)
                if stop == self._send_buffer_fin:
                    self._send_pending_eof = True

    # asyncio.Transport

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        """
        Returns information about the underlying QUIC stream.
        """
        if name == "connection":
            return self._connection
        elif name == "stream_id":
            return self.stream_id

    def write(self, data: bytes) -> None:
        assert self._send_buffer_fin is None, "cannot call write() after FIN"
        size = len(data)

        if size:
            self._send_pending.add(
                self._send_buffer_stop, self._send_buffer_stop + size
            )
            self._send_buffer += data
            self._send_buffer_stop += size
            if self._connection is not None:
                self._connection._send_soon()

    def write_eof(self) -> None:
        assert self._send_buffer_fin is None, "cannot call write_eof() after FIN"

        self._send_buffer_fin = self._send_buffer_stop
        self._send_pending_eof = True
        if self._connection is not None:
            self._connection._send_soon()
