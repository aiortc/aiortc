from typing import Optional

from .mediastreams import VIDEO_CLOCK_RATE
from .rtp import RtpPacket
from .utils import uint16_add, uint32_add, uint32_gt

MAX_MISORDER = 100

# Wait up to one second for a missing video packet. All video uses a 90 kHz
# clock (RFC 7741 section 4.1, RFC 6184 section 5.1).
MAX_VIDEO_WAIT = VIDEO_CLOCK_RATE


class JitterFrame:
    def __init__(self, data: bytes, timestamp: int) -> None:
        self.data = data
        self.timestamp = timestamp


class JitterBuffer:
    """
    A jitter buffer, which puts the RTP packets of a stream back in order and
    assembles them into frames.

    :param capacity: The number of packets the buffer holds, a power of 2.
    :param prefetch: The number of complete frames needed to release the oldest.
    :param is_video: Whether the stream is video. Its frames end on the marker
        bit, and dropping packets calls for a PLI.
    """

    def __init__(
        self, capacity: int, prefetch: int = 0, is_video: bool = False
    ) -> None:
        assert capacity & (capacity - 1) == 0, "capacity must be a power of 2"
        self._capacity = capacity
        self._origin: Optional[int] = None
        self._packets: list[Optional[RtpPacket]] = [None for i in range(capacity)]
        # A frame leaves once prefetch frames are complete, keeping the others back.
        self._keep = max(prefetch - 1, 0)
        self._is_video = is_video

    @property
    def capacity(self) -> int:
        """
        The number of packets the buffer holds.
        """
        return self._capacity

    def add(
        self, packet: RtpPacket, retransmitted: bool = False
    ) -> tuple[bool, list[JitterFrame]]:
        """
        Add a packet and release the frames it completes.

        Set `retransmitted` for a packet which arrived in a retransmission.
        Returns whether a PLI should be sent because packets were dropped, and
        the released frames in order.
        """
        frames: list[JitterFrame] = []
        pli_flag = False
        if self._origin is None:
            self._origin = packet.sequence_number
            delta = 0
            misorder = 0
        else:
            delta = uint16_add(packet.sequence_number, -self._origin)
            misorder = uint16_add(self._origin, -packet.sequence_number)

        if misorder < delta:
            # A retransmission has its own sequence numbers, so one far behind does
            # not show that the sender restarted (RFC 4588 section 4).
            if misorder >= MAX_MISORDER and not retransmitted:
                # Release the complete frames, including the ones kept back.
                frames += self._remove_frames(keep=0)
                self.remove(self.capacity)
                self._origin = packet.sequence_number
                delta = misorder = 0
                if self._is_video:
                    pli_flag = True
            else:
                return pli_flag, []

        if delta >= self.capacity:
            # Release the complete frames, including the ones kept back, then
            # remove just enough frames to fit the received packet.
            frames += self._remove_frames(keep=0)
            delta = uint16_add(packet.sequence_number, -self._origin)
            if delta >= self.capacity:
                excess = delta - self.capacity + 1
                if self.smart_remove(excess):
                    self._origin = packet.sequence_number
                if self._is_video:
                    pli_flag = True

        pos = packet.sequence_number % self._capacity
        self._packets[pos] = packet
        frames += self._remove_frames(keep=self._keep)

        # Give up on a missing video packet whose retransmission is overdue.
        while self._is_video and self._is_overdue(packet):
            # Remove the frame of the first missing packet, which precedes this one.
            gap = 0
            while self._packets[(self._origin + gap) % self._capacity] is not None:
                gap += 1
            self.smart_remove(gap + 1)
            pli_flag = True
            frames += self._remove_frames(keep=self._keep)

        return pli_flag, frames

    def _is_overdue(self, packet: RtpPacket) -> bool:
        """
        Check whether a packet which was just added is more than `MAX_VIDEO_WAIT`
        newer than the oldest packet held.
        """
        # A packet which left the buffer has no missing packet before it.
        if self._packets[packet.sequence_number % self._capacity] is not packet:
            return False

        # Find the oldest packet, which is at worst this one.
        count = 0
        while self._packets[(self._origin + count) % self._capacity] is None:
            count += 1
        oldest = self._packets[(self._origin + count) % self._capacity]
        return uint32_gt(packet.timestamp, uint32_add(oldest.timestamp, MAX_VIDEO_WAIT))

    def _remove_frames(self, keep: int) -> list[JitterFrame]:
        """
        Release the complete frames which precede the first missing packet,
        except the last `keep` of them.
        """
        # Find where the complete frames end, up to the first missing packet.
        ends: list[int] = []
        start = 0
        timestamp = None

        for count in range(self.capacity):
            pos = (self._origin + count) % self._capacity
            packet = self._packets[pos]
            if packet is None:
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

        # Release the complete frames, except the last ones to keep back.
        frames: list[JitterFrame] = []
        start = 0
        for end in ends[: max(len(ends) - keep, 0)]:
            packets = [
                self._packets[(self._origin + i) % self._capacity]
                for i in range(start, end)
            ]
            frames.append(
                JitterFrame(
                    data=b"".join([x._data for x in packets]),
                    timestamp=packets[0].timestamp,
                )
            )
            start = end
        self.remove(start)
        return frames

    def remove(self, count: int) -> None:
        """
        Remove `count` packets from the start of the buffer.
        """
        assert count <= self._capacity
        for i in range(count):
            pos = self._origin % self._capacity
            self._packets[pos] = None
            self._origin = uint16_add(self._origin, 1)

    def smart_remove(self, count: int) -> bool:
        """
        Remove at least `count` packets from the start of the buffer, then the
        rest of the frame they end in, so that no partial frame is decoded.

        Returns whether the whole buffer was emptied.
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
