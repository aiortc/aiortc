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
    convert_timebase,
)
from av import AudioFrame
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
            wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.monotonic()
            await asyncio.sleep(wait)
        else:
            self._start = time.monotonic()
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


class ConvertTimebaseTest(TestCase):
    def test_same_base(self) -> None:
        self.assertEqual(convert_timebase(1234, VIDEO_TIME_BASE, VIDEO_TIME_BASE), 1234)

    def test_fraction(self) -> None:
        self.assertEqual(
            convert_timebase(960, fractions.Fraction(1, 48000), VIDEO_TIME_BASE),
            1800,
        )

    def test_pyav_time_base(self) -> None:
        # PyAV >= 19 returns an AVRational rather than a Fraction.
        frame = AudioFrame(format="s16", layout="mono", samples=960)
        frame.time_base = fractions.Fraction(1, 48000)
        self.assertEqual(convert_timebase(960, frame.time_base, VIDEO_TIME_BASE), 1800)

    def test_negative_pts_truncates_toward_zero(self) -> None:
        self.assertEqual(
            convert_timebase(-1, fractions.Fraction(1, 2), fractions.Fraction(1, 1)),
            0,
        )
