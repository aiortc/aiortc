from typing import Optional

from .rtp import RtpPacket
from .utils import uint16_add, uint16_gt

MAX_MISORDER = 100
MAX_FRAME_PACKETS = 256
MAX_PENDING_FRAMES = 16


class JitterFrame:
    def __init__(self, data: bytes, timestamp: int) -> None:
        self.data = data
        self.timestamp = timestamp


class JitterBuffer:
    def __init__(
        self,
        capacity: int,
        prefetch: int = 0,
        is_video: bool = False,
        reorder_capacity: int = 1,
    ) -> None:
        self._capacity = capacity
        self._prefetch = prefetch
        self._is_video = is_video
        self._reorder_capacity = max(1, reorder_capacity)

        # Reorder buffer (maintained sorted by sequence number)
        self._buffer: list[RtpPacket] = []
        self._last_emitted_seq: Optional[int] = None

        # Frame assembler state
        self._frame_packets: list[RtpPacket] = []
        self._frame_timestamp: Optional[int] = None
        self._pending_frames: list[JitterFrame] = []
        self._pli_flag = False

    @property
    def capacity(self) -> int:
        return self._capacity

    def add(
        self, packet: RtpPacket, arrival_time_ms: int = 0
    ) -> tuple[bool, Optional[JitterFrame]]:
        self._pli_flag = False

        # Check if packet is too old, duplicate, or stream reset
        if self._last_emitted_seq is not None:
            if packet.sequence_number == self._last_emitted_seq:
                return self._pli_flag, None  # duplicate of last emitted
            delta = uint16_add(packet.sequence_number, -self._last_emitted_seq)
            misorder = uint16_add(self._last_emitted_seq, -packet.sequence_number)
            if misorder < delta:
                if misorder >= MAX_MISORDER:
                    self._reset()
                    if self._is_video:
                        self._pli_flag = True
                else:
                    return self._pli_flag, None
        elif self._buffer:
            # Only check for stream reset (very large backward jump).
            # Do NOT drop reordered packets — they should be inserted
            # into the sorted buffer.
            min_seq = self._buffer[0].sequence_number
            misorder = uint16_add(min_seq, -packet.sequence_number)
            delta = uint16_add(packet.sequence_number, -min_seq)
            if misorder < delta and misorder >= MAX_MISORDER:
                self._reset()
                if self._is_video:
                    self._pli_flag = True

        # Insert into reorder buffer (sorted by seq)
        self._insert_sorted(packet)

        # Handle buffer overflow (seq span exceeds capacity)
        if len(self._buffer) >= 2:
            span = uint16_add(
                self._buffer[-1].sequence_number,
                -self._buffer[0].sequence_number,
            )
            if span >= self._capacity:
                if self._is_video:
                    self._pli_flag = True
                while len(self._buffer) >= 2:
                    span = uint16_add(
                        self._buffer[-1].sequence_number,
                        -self._buffer[0].sequence_number,
                    )
                    if span < self._capacity:
                        break
                    self._emit_one()

        # Emit packets when reorder buffer is full
        while len(self._buffer) >= self._reorder_capacity:
            self._emit_one()

        return self._pli_flag, self._take_frame()

    def _reset(self) -> None:
        self._buffer.clear()
        self._last_emitted_seq = None
        self._frame_packets.clear()
        self._frame_timestamp = None
        self._pending_frames.clear()

    def _insert_sorted(self, packet: RtpPacket) -> None:
        seq = packet.sequence_number
        for i, p in enumerate(self._buffer):
            if p.sequence_number == seq:
                return  # duplicate
            if uint16_gt(p.sequence_number, seq):
                self._buffer.insert(i, packet)
                return
        self._buffer.append(packet)

    def _emit_one(self) -> None:
        if not self._buffer:
            return
        packet = self._buffer.pop(0)

        # Check for gap (confirmed loss)
        if self._last_emitted_seq is not None:
            expected = uint16_add(self._last_emitted_seq, 1)
            if expected != packet.sequence_number:
                if self._is_video and not self._pli_flag:
                    self._pli_flag = True
                # Discard incomplete frame being assembled
                self._frame_packets.clear()
                self._frame_timestamp = None

        self._last_emitted_seq = packet.sequence_number

        # Memory protection: discard oversized frame
        if len(self._frame_packets) >= MAX_FRAME_PACKETS:
            if self._is_video and not self._pli_flag:
                self._pli_flag = True
            self._frame_packets.clear()
            self._frame_timestamp = None

        # Frame assembly: group packets by timestamp
        if (
            self._frame_timestamp is not None
            and packet.timestamp != self._frame_timestamp
        ):
            # Timestamp changed - previous frame is complete
            self._complete_frame()
            self._frame_packets = [packet]
            self._frame_timestamp = packet.timestamp
        else:
            if self._frame_timestamp is None:
                self._frame_timestamp = packet.timestamp
            self._frame_packets.append(packet)

        # Marker bit: immediate frame completion
        if getattr(packet, "marker", 0):
            self._complete_frame()

    def _complete_frame(self) -> None:
        """Finalize the current frame and add to pending."""
        if not self._frame_packets:
            return
        frame = JitterFrame(
            data=b"".join([p._data for p in self._frame_packets]),  # type: ignore
            timestamp=self._frame_timestamp,
        )
        self._pending_frames.append(frame)
        self._frame_packets = []
        self._frame_timestamp = None

        # Memory protection: cap pending frames
        while len(self._pending_frames) > MAX_PENDING_FRAMES:
            self._pending_frames.pop(0)

    def _take_frame(self) -> Optional[JitterFrame]:
        if len(self._pending_frames) >= max(1, self._prefetch):
            return self._pending_frames.pop(0)
        return None

    def flush(self) -> list[JitterFrame]:
        """Flush all remaining buffered packets. Call on stream end."""
        frames: list[JitterFrame] = []
        while self._buffer:
            self._emit_one()
        if self._frame_packets:
            self._complete_frame()
        frames.extend(self._pending_frames)
        self._pending_frames.clear()
        return frames
