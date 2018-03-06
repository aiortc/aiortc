MAX_MISORDER = 100
MAX_DROPOUT = 3000


class JitterFrame:
    def __init__(self, payload, sequence_number, timestamp):
        self.payload = payload
        self.sequence_number = sequence_number
        self.timestamp = timestamp

    def __repr__(self):
        return 'JitterFrame(seq=%d, ts=%d)' % (self.sequence_number, self.timestamp)


class JitterBuffer:
    def __init__(self, capacity):
        self._capacity = capacity
        self._frames = [None for i in range(capacity)]
        self._head = 0
        self._origin = None

    @property
    def capacity(self):
        return self._capacity

    def add(self, payload, sequence_number, timestamp):
        if self._origin is None:
            self._origin = sequence_number
        elif sequence_number <= self._origin - MAX_MISORDER:
            self.__reset()
            self._origin = sequence_number
        elif sequence_number < self._origin:
            return

        delta = sequence_number - self._origin
        if delta >= self._capacity:
            if delta > MAX_DROPOUT:
                self.__reset()
                self._origin = sequence_number
                delta = 0
            else:
                return

        pos = (self._head + delta) % self._capacity
        self._frames[pos] = JitterFrame(payload=payload,
                                        sequence_number=sequence_number,
                                        timestamp=timestamp)

    def peek(self, offset):
        if offset >= self._capacity:
            raise IndexError('Cannot peek at offset %d, capacity is %d' % (offset, self._capacity))
        pos = (self._head + offset) % self._capacity
        return self._frames[pos]

    def remove(self, count):
        assert count <= self._capacity
        frames = [None for i in range(count)]
        for i in range(count):
            frames[i] = self._frames[self._head]
            self._frames[self._head] = None
            self._head = (self._head + 1) % self._capacity
            self._origin += 1
        return frames

    def __reset(self):
        self._head = 0
        self._origin = None

        for i in range(self._capacity):
            self._frames[i] = None
