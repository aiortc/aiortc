from .packet import QuicStreamFrame
from .rangeset import RangeSet


class QuicStream:
    def __init__(self):
        self._buffer = bytearray()
        self._received = RangeSet()
        self._start = 0

    def add_frame(self, frame: QuicStreamFrame):
        pos = frame.offset - self._start
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
        self._received.add(frame.offset, frame.offset + count)

        # add data
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
