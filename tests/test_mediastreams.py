import asyncio
import fractions
import time
from unittest import TestCase

from aiortc.mediastreams import (
    VIDEO_CLOCK_RATE,
    VIDEO_PTIME,
    VIDEO_TIME_BASE,
    AudioStreamTrack,
    MediaStreamTrack,
    VideoStreamTrack,
)
from av.packet import Packet


class VideoPacketStreamTrack(MediaStreamTrack):
    """
    A dummy video native track which reads green frames.
    """

    kind = "video"

    _start: float
    _timestamp: int

    async def next_timestamp(self) -> tuple[int, fractions.Fraction]:
        if hasattr(self, "_timestamp"):
            self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
            wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.time()
            await asyncio.sleep(wait)
        else:
            self._start = time.time()
            self._timestamp = 0
        return self._timestamp, VIDEO_TIME_BASE

    async def recv(self) -> Packet:
        """
        Receive the next :class:`~av.packet.Packet`.

        The base implementation dummy packet h264 for tests
        """
        pts, time_base = await self.next_timestamp()
        header = [0, 0, 0, 1]
        buffer = header + [0] * 1020
        packet = Packet(len(buffer))
        packet.update(bytes(buffer))
        packet.pts = pts
        packet.time_base = time_base
        return packet


class MediaStreamTrackTest(TestCase):
    def test_audio(self) -> None:
        track = AudioStreamTrack()
        self.assertEqual(track.kind, "audio")
        self.assertEqual(len(track.id), 36)

    def test_video(self) -> None:
        track = VideoStreamTrack()
        self.assertEqual(track.kind, "video")
        self.assertEqual(len(track.id), 36)

    def test_native_video(self) -> None:
        track = VideoPacketStreamTrack()
        self.assertEqual(track.kind, "video")
        self.assertEqual(len(track.id), 36)
