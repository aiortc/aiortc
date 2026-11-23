from typing import Optional

from .rtp import RtpPacket
from .utils import uint16_add

MAX_MISORDER = 100


class JitterFrame:
    def __init__(self, data: bytes, timestamp: int) -> None:
        self.data = data
        self.timestamp = timestamp


class JitterBuffer:
    def __init__(
        self, capacity: int, prefetch: int = 0, is_video: bool = False
    ) -> None:
        assert capacity & (capacity - 1) == 0, "capacity must be a power of 2"
        self._capacity = capacity
        self._origin: Optional[int] = None
        self._packets: list[Optional[RtpPacket]] = [None for i in range(capacity)]
        self._prefetch = prefetch
        self._is_video = is_video

    @property
    def capacity(self) -> int:
        return self._capacity

    def add(self, packet: RtpPacket) -> tuple[bool, Optional[JitterFrame]]:
        pli_flag = False
        if self._origin is None:
            self._origin = packet.sequence_number
            delta = 0
            misorder = 0
        else:
            delta = uint16_add(packet.sequence_number, -self._origin)
            misorder = uint16_add(self._origin, -packet.sequence_number)

        if misorder < delta:
            if misorder >= MAX_MISORDER:
                self.remove(self.capacity)
                self._origin = packet.sequence_number
                delta = misorder = 0
                if self._is_video:
                    pli_flag = True
            else:
                return pli_flag, None

        if delta >= self.capacity:
            # remove just enough frames to fit the received packets
            excess = delta - self.capacity + 1
            if self.smart_remove(excess):
                self._origin = packet.sequence_number
            if self._is_video:
                pli_flag = True

        pos = packet.sequence_number % self._capacity
        self._packets[pos] = packet

        return pli_flag, self._remove_frame(packet.sequence_number)

    def _remove_frame(self, sequence_number: int) -> Optional[JitterFrame]:
        # Find where the complete frames end, up to the first missing packet.
        ends: list[int] = []
        start = 0
        timestamp = None

        for count in range(self.capacity):
            pos = (self._origin + count) % self._capacity
            packet = self._packets[pos]
            if packet is None or len(ends) >= max(self._prefetch, 1):
                break
            if count == start:
                timestamp = packet.timestamp
            elif packet.timestamp != timestamp:
                ends.append(count)
                start = count
                timestamp = packet.timestamp
            if self._is_video and packet.marker:
                # The marker bit flags the last packet of a video frame
                # (RFC 7741 section 4.1, RFC 6184 section 5.1).
                ends.append(count + 1)
                start = count + 1

        # Check we have prefetched enough, then remove the first frame.
        if len(ends) < max(self._prefetch, 1):
            return None
        packets = [
            self._packets[(self._origin + i) % self._capacity] for i in range(ends[0])
        ]
        frame = JitterFrame(
            data=b"".join([x._data for x in packets]),
            timestamp=packets[0].timestamp,
        )
        self.remove(ends[0])
        return frame

    def remove(self, count: int) -> None:
        assert count <= self._capacity
        for i in range(count):
            pos = self._origin % self._capacity
            self._packets[pos] = None
            self._origin = uint16_add(self._origin, 1)

    def smart_remove(self, count: int) -> bool:
        """
        Makes sure that all packages belonging to the same frame are removed
        to prevent sending corrupted frames to the decoder.
        """
        timestamp = None
        for i in range(self._capacity):
            pos = self._origin % self._capacity
            packet = self._packets[pos]
            if packet is not None:
                if i >= count and timestamp != packet.timestamp:
                    break
                timestamp = packet.timestamp
            self._packets[pos] = None
            self._origin = uint16_add(self._origin, 1)
            if i == self._capacity - 1:
                return True
        return False
