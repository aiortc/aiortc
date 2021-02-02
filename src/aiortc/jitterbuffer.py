import asyncio
from typing import List, Optional

from .rtp import RtpPacket

MAX_MISORDER = 100


class JitterFrame:
    def __init__(self, data: bytes, timestamp: int) -> None:
        self.data = data
        self.timestamp = timestamp


class JitterBuffer:
    def __init__(self, capacity: int, prefetch: int = 0, sendPLI = None) -> None:
        assert capacity & (capacity - 1) == 0, "capacity must be a power of 2"
        self._capacity = capacity
        self._origin: Optional[int] = None
        self._packets: List[Optional[RtpPacket]] = [None for i in range(capacity)]
        self._prefetch = prefetch
        self.__max_number = 65536
        self.sendPLI = sendPLI

    @property
    def capacity(self) -> int:
        return self._capacity

    def add(self, packet: RtpPacket) -> Optional[JitterFrame]:
#        print("[INFO][JitterBuffer] Received Seq:",packet.sequence_number)
        delta = 0
        if self._origin is None:
            self._origin = packet.sequence_number
        elif self._origin <  MAX_MISORDER and packet.sequence_number > self.__max_number - MAX_MISORDER:
            if self._origin >= (packet.sequence_number + MAX_MISORDER)%self.__max_number:
#                print("[INFO][JitterBuffer] Received Seq1:",packet.sequence_number,"In Origin" ,self._origin, 'max_number of ',self.__max_number)
                self.remove(self.capacity)
                self._origin = packet.sequence_number
                if self.sendPLI != None:
                    asyncio.ensure_future(self.sendPLI(packet.ssrc))
            else:
                return None
        elif packet.sequence_number <  MAX_MISORDER  and self._origin > self.__max_number - MAX_MISORDER:
            delta = (packet.sequence_number - self._origin)%self.__max_number
        else:
            if packet.sequence_number <= self._origin - MAX_MISORDER:
#                print("[INFO][JitterBuffer] Received Seq2:",packet.sequence_number,"In Origin" ,self._origin, 'max_number of ',self.__max_number)
                self.remove(self.capacity)
                self._origin = packet.sequence_number
                if self.sendPLI != None:
                    asyncio.ensure_future(self.sendPLI(packet.ssrc))
            elif packet.sequence_number < self._origin:
                return None
            else:
                delta = (packet.sequence_number - self._origin)%self.__max_number
#        print("[INFO][JitterBuffer] Received Seq3:",packet.sequence_number,"In Origin" ,self._origin, 'delta ',delta)

        if delta >= 2 * self.capacity:
            # received packet is so far beyond capacity we cannot keep any
            # previous packets, so reset the buffer
            self.remove(self.capacity)
            self._origin = packet.sequence_number
            if self.sendPLI != None:
                asyncio.ensure_future(self.sendPLI(packet.ssrc))
        elif delta >= self.capacity:
            # remove just enough packets to fit the received packets
            excess = delta - self.capacity + 1
            self.remove(excess)

        pos = packet.sequence_number % self._capacity
        self._packets[pos] = packet

        return self._remove_frame(packet.sequence_number)

    def _remove_frame(self, sequence_number: int) -> Optional[JitterFrame]:
        frame = None
        frames = 0
        packets = []
        remove = 0
        timestamp = None

        for count in range(self.capacity):
            pos = (self._origin + count) % self._capacity
            packet = self._packets[pos]
            if packet is None:
                break
            if timestamp is None:
                timestamp = packet.timestamp
            elif packet.timestamp != timestamp:
                # we now have a complete frame, only store the first one
                if frame is None:
                    frame = JitterFrame(
                        data=b"".join([x._data for x in packets]), timestamp=timestamp
                    )
                    remove = count

                # check we have prefetched enough
                frames += 1
                if frames >= self._prefetch:
                    self.remove(remove)
                    return frame

                # start a new frame
                packets = []
                timestamp = packet.timestamp

            packets.append(packet)

        return None

    def remove(self, count: int) -> None:
        assert count <= self._capacity
        for i in range(count):
            pos = self._origin % self._capacity
            self._packets[pos] = None
            self._origin = (self._origin + 1)%self.__max_number
