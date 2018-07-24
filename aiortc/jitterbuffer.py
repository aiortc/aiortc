MAX_MISORDER = 100


class JitterFrame:
    def __init__(self, payloads, timestamp):
        self.payloads = payloads
        self.timestamp = timestamp


class JitterBuffer:
    def __init__(self, capacity):
        self._capacity = capacity
        self._head = 0
        self._origin = None
        self._packets = [None for i in range(capacity)]

    @property
    def capacity(self):
        return self._capacity

    def add(self, packet):
        if self._origin is None:
            self._origin = packet.sequence_number
        elif packet.sequence_number <= self._origin - MAX_MISORDER:
            self.__reset()
            self._origin = packet.sequence_number
        elif packet.sequence_number < self._origin:
            return

        delta = packet.sequence_number - self._origin
        if delta >= 2 * self.capacity:
            # received packet is so far beyond capacity we cannot keep any
            # previous packets, so reset the buffer
            self.__reset()
            self._origin = packet.sequence_number
            delta = 0
        elif delta >= self.capacity:
            # remove just enough packets to fit the received packets
            excess = delta - self.capacity + 1
            self.remove(excess)
            delta = packet.sequence_number - self._origin

        pos = (self._head + delta) % self._capacity
        self._packets[pos] = packet

    def remove_frame(self):
        timestamp = None
        payloads = []

        for count in range(self.capacity):
            pos = (self._head + count) % self._capacity
            packet = self._packets[pos]
            if packet is None:
                break
            if timestamp is None:
                timestamp = packet.timestamp
            elif packet.timestamp != timestamp:
                self.remove(count)
                return JitterFrame(payloads=payloads, timestamp=timestamp)
            payloads.append(packet.payload)

    def remove(self, count):
        assert count <= self._capacity
        for i in range(count):
            self._packets[self._head] = None
            self._head = (self._head + 1) % self._capacity
            self._origin += 1

    def __reset(self):
        self._head = 0
        self._origin = None

        for i in range(self._capacity):
            self._packets[i] = None
