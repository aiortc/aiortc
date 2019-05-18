import asyncio
from typing import Any, Optional

from .packet import QuicStreamFrame
from .rangeset import RangeSet


class QuicStream:
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
        self._recv_start = 0
        self._recv_ranges = RangeSet()

        self._send_buffer = bytearray()
        self._send_fin = False
        self._send_start = 0

        self.__stream_id = stream_id

    @property
    def stream_id(self) -> Optional[int]:
        return self.__stream_id

    def add_frame(self, frame: QuicStreamFrame) -> None:
        """
        Add a frame of received data.
        """
        pos = frame.offset - self._recv_start
        count = len(frame.data)

        if pos + count > 0:
            # frame has been partially consumed
            if pos < 0:
                count += pos
                frame.data = frame.data[-pos:]
                frame.offset -= pos
                pos = 0

            # marked received
            if count:
                self._recv_ranges.add(frame.offset, frame.offset + count)

            # add data
            gap = pos - len(self._recv_buffer)
            if gap > 0:
                self._recv_buffer += bytearray(gap)
            self._recv_buffer[pos : pos + count] = frame.data

        if self.reader:
            if self.has_data_to_read():
                self.reader.feed_data(self.pull_data())
            if frame.fin:
                self.reader.feed_eof()

    def connection_lost(self, exc: Exception) -> None:
        if self.reader is not None:
            if exc is None:
                self.reader.feed_eof()
            else:
                self.reader.set_exception(exc)

    def get_frame(self, size: int) -> QuicStreamFrame:
        """
        Get a frame of data to send.
        """
        size = min(size, len(self._send_buffer))
        frame = QuicStreamFrame(data=self._send_buffer[:size], offset=self._send_start)
        frame.fin = self._send_fin
        self._send_buffer = self._send_buffer[size:]
        self._send_fin = False
        self._send_start += size
        return frame

    def has_data_to_read(self) -> bool:
        return (
            bool(self._recv_ranges) and self._recv_ranges[0].start == self._recv_start
        )

    def has_data_to_send(self) -> bool:
        return bool(self._send_buffer) or self._send_fin

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
        if data:
            self._send_buffer += data
            if self._connection is not None:
                self._connection._send_soon()

    def write_eof(self) -> None:
        self._send_fin = True
        self._connection._send_soon()
