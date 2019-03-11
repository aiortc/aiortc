from .packet import QuicStreamFrame
from .rangeset import RangeSet


class QuicStream:
    def __init__(self):
        self._buffer = bytearray()
        self._received = RangeSet()
        self._start = 0

    def add_frame(self, frame: QuicStreamFrame):
        assert frame.offset >= self._start

        # marked received
        count = len(frame.data)
        self._received.add(frame.offset, frame.offset + count)

        # add data
        pos = frame.offset - self._start
        gap = pos - len(self._buffer)
        if gap > 0:
            self._buffer += bytearray(gap)
        self._buffer[pos:pos + count] = frame.data

    def pull_data(self):
        # no data, or gap at start
        if not self._received or self._received[0].start != self._start:
            return b''

        r = self._received.shift()
        pos = r.stop - r.start
        data = self._buffer[:pos]
        self._buffer = self._buffer[pos:]
        self._start = r.stop
        return data
