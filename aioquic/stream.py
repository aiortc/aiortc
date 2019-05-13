import asyncio
from typing import Any, Optional

from .packet import QuicStreamFrame
from .rangeset import RangeSet


class QuicStream:
    """
    A QUIC stream.

    Do not instanciate this class yourself, instead use :meth:`QuicConnection.create_stream`.
    """

    def __init__(
        self, stream_id: Optional[int] = None, connection: Optional[Any] = None
    ) -> None:
        self._connection = connection
        self._eof = False
        self._loop = asyncio.get_event_loop()

        self._recv_buffer = bytearray()
        self._recv_start = 0
        self._recv_ranges = RangeSet()
        self._recv_waiter: Optional[asyncio.Future[Any]] = None

        self._send_buffer = bytearray()
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

        # frame has been entirely consumed
        if pos + count <= 0:
            return

        # frame has been partially consumed
        if pos < 0:
            count += pos
            frame.data = frame.data[-pos:]
            frame.offset -= pos
            pos = 0

        # marked received
        self._recv_ranges.add(frame.offset, frame.offset + count)

        # add data
        gap = pos - len(self._recv_buffer)
        if gap > 0:
            self._recv_buffer += bytearray(gap)
        self._recv_buffer[pos : pos + count] = frame.data

        if not pos:
            self._wakeup_waiter()

    def feed_eof(self) -> None:
        self._eof = True
        self._wakeup_waiter()

    def get_frame(self, size: int) -> QuicStreamFrame:
        """
        Get a frame of data to send.
        """
        size = min(size, len(self._send_buffer))
        frame = QuicStreamFrame(data=self._send_buffer[:size], offset=self._send_start)
        self._send_buffer = self._send_buffer[size:]
        self._send_start += size
        return frame

    def has_data_to_send(self) -> bool:
        return bool(self._send_buffer)

    def pull_data(self) -> bytes:
        """
        Pull received data.
        """
        # no data, or gap at start
        if not self._recv_ranges or self._recv_ranges[0].start != self._recv_start:
            return b""

        r = self._recv_ranges.shift()
        pos = r.stop - r.start
        data = self._recv_buffer[:pos]
        self._recv_buffer = self._recv_buffer[pos:]
        self._recv_start = r.stop
        return data

    async def read(self) -> bytes:
        """
        Read data from the stream.
        """
        if (
            not self._recv_ranges
            or self._recv_ranges[0].start != self._recv_start
            and not self._eof
        ):
            assert self._recv_waiter is None
            self._recv_waiter = self._loop.create_future()
            try:
                await self._recv_waiter
            finally:
                self._recv_waiter = None

        return self.pull_data()

    def _wakeup_waiter(self) -> None:
        """
        Wakeup read() function.
        """
        waiter = self._recv_waiter
        if waiter is not None:
            self._recv_waiter = None
            if not waiter.cancelled():
                waiter.set_result(None)

    def write(self, data: bytes) -> None:
        """
        Write some `data` bytes to the stream.

        This method does not block; it buffers the data and arranges for it to
        be sent out asynchronously.
        """
        if data:
            self._send_buffer += data
            if self._connection is not None:
                self._connection._send_pending()
