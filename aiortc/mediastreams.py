import asyncio
import fractions
import time
import uuid

from av import AudioFrame, VideoFrame
from pyee import AsyncIOEventEmitter

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
SLEEP_THRESHOLD = VIDEO_PTIME / 2
FRAME_TS_THRESHOLD = VIDEO_PTIME / 10


def convert_timebase(pts, from_base, to_base):
    if from_base != to_base:
        pts = int(pts * from_base / to_base)
    return pts


class MediaStreamError(Exception):
    pass


class MediaStreamTrack(AsyncIOEventEmitter):
    """
    A single media track within a stream.

    See :class:`AudioStreamTrack` and :class:`VideoStreamTrack`.
    """

    def __init__(self):
        super().__init__()
        self.__ended = False
        self.__id = str(uuid.uuid4())
        self.should_by_pass_encoder = False

    @property
    def id(self):
        """
        An automatically generated globally unique ID.
        """
        return self.__id

    @property
    def readyState(self):
        return "ended" if self.__ended else "live"

    def stop(self):
        if not self.__ended:
            self.__ended = True
            self.emit("ended")

            # no more events will be emitted, so remove all event listeners
            # to facilitate garbage collection.
            self.remove_all_listeners()


class AudioStreamTrack(MediaStreamTrack):
    """
    An audio track.
    """

    kind = "audio"

    async def recv(self):
        """
        Receive the next :class:`~av.audio.frame.AudioFrame`.

        The base implementation just reads silence, subclass
        :class:`AudioStreamTrack` to provide a useful implementation.
        """
        if self.readyState != "live":
            raise MediaStreamError

        sample_rate = 8000
        samples = int(AUDIO_PTIME * sample_rate)

        if hasattr(self, "_timestamp"):
            self._timestamp += samples
            wait = self._start + (self._timestamp / sample_rate) - time.time()
            await asyncio.sleep(wait)
        else:
            self._start = time.time()
            self._timestamp = 0

        frame = AudioFrame(format="s16", layout="mono", samples=samples)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = self._timestamp
        frame.sample_rate = sample_rate
        frame.time_base = fractions.Fraction(1, sample_rate)
        return frame


class VideoStreamTrack(MediaStreamTrack):
    """
    A video stream track.
    """

    kind = "video"

    async def next_timestamp(self):
        if self.readyState != "live":
            raise MediaStreamError

        if hasattr(self, "_timestamp"):
            self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
            wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.time()
            await asyncio.sleep(wait)
        else:
            self._start = time.time()
            self._timestamp = 0
        return self._timestamp, VIDEO_TIME_BASE

    async def recv(self):
        """
        Receive the next :class:`~av.video.frame.VideoFrame`.

        The base implementation just reads a 640x480 green frame at 30fps,
        subclass :class:`VideoStreamTrack` to provide a useful implementation.
        """
        pts, time_base = await self.next_timestamp()

        frame = VideoFrame(width=640, height=480)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.time_base = time_base
        return frame


class EncodedVideoStreamTrack(MediaStreamTrack):
    """
    A video stream track.
    """

    kind = "video"

    def __init__(self, uds_address):
        """
        Add a :class:`StreamReader` to the set of encoded media tracks which
        will be transmitted to the remote peer.
        """
        super().__init__()
        self.should_by_pass_encoder = True
        self.__uds_address = uds_address
        self.__most_recent_frame = None
        self.__buffer = []
        self.__last_packet_ts = 0
        print("EncodedVideoStreamTrack Created")
        self.__reading_task_running = False
        self._timestamp = 0

    async def _reading_and_parsing_frames(self):
        self.__stream_reader, self.__stream_writer = await asyncio.open_unix_connection(self.__uds_address)
        self.__last_packet_ts = time.time()
        while True:
            data = await self.__stream_reader.read(102400)
            if len(data) > 0:
                ts = time.time()
                if ts - self.__last_packet_ts > FRAME_TS_THRESHOLD and len(self.__buffer) > 0:
                    # check if this is next frame (interval larger than gap)
                    # if this is new frame, put all data in the buffer to the output queue
                    self.__most_recent_frame = b"".join(p for p in self.__buffer)

                    # for debugging purpose
                    total_data_length = sum(map(lambda d: len(d), self.__buffer))
                    print("length: " + str(total_data_length) + " at " + str(len(self.__buffer)) + " packages at ts " + str(ts))
                    # end debugging

                    self.__buffer.clear()

                # append the data to the buffer
                self.__buffer.append(data)
                self.__last_packet_ts = ts

            # sleep to wait for data
            await asyncio.sleep(SLEEP_THRESHOLD)

    async def recv(self):
        """
        Receive the next :class:`~av.video.frame.VideoFrame`.

        The base implementation just reads a 640x480 green frame at 30fps,
        subclass :class:`VideoStreamTrack` to provide a useful implementation.
        """
        print("called")
        if not self.__reading_task_running:
            print("kicking off task")
            asyncio.create_task(self._reading_and_parsing_frames())
            print("start to read from uds")
            self.__reading_task_running = True
        while self.__most_recent_frame is None:
            await asyncio.sleep(SLEEP_THRESHOLD)
        frame = self.__most_recent_frame
        self.__most_recent_frame = None

        # calculate ts
        self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)

        print("frame read")
        return frame, self._timestamp
