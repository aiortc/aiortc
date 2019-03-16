from .packet import QuicStreamFrame
from .rangeset import RangeSet


class QuicStream:
    def __init__(self, stream_id=None):
        self._recv_buffer = bytearray()
        self._recv_start = 0
        self._recv_ranges = RangeSet()

        self._send_buffer = bytearray()
        self._send_start = 0

        self.__stream_id = stream_id

    @property
    def stream_id(self):
        return self.__stream_id

    def add_frame(self, frame: QuicStreamFrame):
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
        self._recv_buffer[pos:pos + count] = frame.data

    def get_frame(self, size):
        """
        Get a frame of data to send.
        """
        size = min(size, len(self._send_buffer))
        frame = QuicStreamFrame(data=self._send_buffer[:size], offset=self._send_start)
        self._send_buffer = self._send_buffer[size:]
        self._send_start += size
        return frame

    def has_data_to_send(self):
        return bool(self._send_buffer)

    def pull_data(self):
        """
        Pull received data.
        """
        # no data, or gap at start
        if not self._recv_ranges or self._recv_ranges[0].start != self._recv_start:
            return b''

        r = self._recv_ranges.shift()
        pos = r.stop - r.start
        data = self._recv_buffer[:pos]
        self._recv_buffer = self._recv_buffer[pos:]
        self._recv_start = r.stop
        return data

    def push_data(self, data):
        """
        Push data to send.
        """
        if data:
            self._send_buffer += data
