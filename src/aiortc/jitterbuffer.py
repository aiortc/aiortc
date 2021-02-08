import asyncio
from typing import List, Optional

from .rtp import RtpPacket

MAX_MISORDER = 100


class JitterFrame:
    def __init__(self, data: bytes, timestamp: int) -> None:
        self.data = data
        self.timestamp = timestamp


class JitterBuffer:
    def __init__(self, capacity: int, prefetch: int = 0, sendPLI=None) -> None:
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
        # print("[INFO][JitterBuffer] Received Seq:", packet.sequence_number)
        if self._origin is None:
            self._origin = packet.sequence_number
            delta = 0
            misorder = 0
        else:
            delta = (packet.sequence_number - self._origin) % self.__max_number
            misorder = (self._origin - packet.sequence_number) % self.__max_number

        if misorder < delta:
            if misorder >= MAX_MISORDER:
                # print("[INFO][JitterBuffer] Received Seq1:", packet.sequence_number, " Origin:", self._origin,
                #       ' misorder:', self.__max_number)
                self.smart_remove(self.capacity, dumb_mode=True)
                self._origin = packet.sequence_number
                delta = misorder = 0
                if self.sendPLI is not None:
                    asyncio.ensure_future(self.sendPLI(packet.ssrc))
            else:
                return None

        # print("[INFO][JitterBuffer] Received Seq2:", packet.sequence_number, " Origin:", self._origin,
        #       ' delta:', delta)

        if delta >= self.capacity:
            # remove just enough frames to fit the received packets
            excess = delta - self.capacity + 1
            print("[WARNING][JitterBuffer] At least:", excess, "packets will be lost")
            if self.smart_remove(excess):
                self._origin = packet.sequence_number
            if self.sendPLI is not None:
                asyncio.ensure_future(self.sendPLI(packet.ssrc))

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
                    # self.smart_remove(remove, dumb_mode=True) # "remove" might still be a bit faster
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
            self._origin = (self._origin + 1) % self.__max_number

    def smart_remove(self, count: int, dumb_mode: bool = False) -> bool:
        # smart_remove makes sure that all packages belonging to the same frame are removed
        # it prevents sending corrupted frames to decoder
        timestamp = None
        for i in range(self._capacity):
            pos = self._origin % self._capacity
            packet = self._packets[pos]
            if dumb_mode:
                if i == count:
                    break
            elif packet is not None:
                if i >= count and timestamp != packet.timestamp:
                    break
                timestamp = packet.timestamp
            self._packets[pos] = None
            self._origin = (self._origin + 1) % self.__max_number
            if i == self._capacity - 1:
                print("[Warning][JitterBuffer] JitterBuffer purged !!!")
                return True
        return False
