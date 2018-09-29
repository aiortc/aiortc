import asyncio
import math
import uuid

from pyee import EventEmitter

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCKRATE = 90000
VIDEO_PTIME = 1 / 30  # 30fps


class AudioFrame:
    """
    Audio frame, 16-bit PCM.
    """
    def __init__(self, channels, data, sample_rate, timestamp):
        self.channels = channels
        "The number of channels (`1` for mono, `2` for stereo)."
        self.data = data
        "The bytes representing the PCM samples."
        self.sample_rate = sample_rate
        "The sample rate, for instance `48000` for 48kHz."
        self.sample_width = 2
        "The sample width in bytes, always `2` (16-bit)."
        self.timestamp = timestamp


class VideoFrame:
    """
    Video frame in YUV420 planar format.
    """
    def __init__(self, width, height, timestamp, data=None):
        assert width % 2 == 0, 'Frame width must be a multiple of 2'
        assert height % 2 == 0, 'Frame height must be a multiple of 2'

        data_size = math.ceil(width * height * 12 / 8)

        if data is None:
            data = bytes(data_size)
        else:
            assert len(data) == data_size, 'Frame data size does not match frame dimensions'

        self.data = data
        "The bytes representing the pixels."
        self.width = width
        "The image width in pixels."
        self.height = height
        "The image height in pixels."
        self.timestamp = timestamp


class MediaStreamTrack(EventEmitter):
    """
    A single media track within a stream.

    See :class:`AudioStreamTrack` and :class:`VideoStreamTrack`.
    """
    def __init__(self):
        super().__init__()
        self.__id = str(uuid.uuid4())

    @property
    def id(self):
        """
        An automatically generated globally unique ID.
        """
        return self.__id


class AudioStreamTrack(MediaStreamTrack):
    """
    An audio track.
    """
    kind = 'audio'

    async def recv(self):
        """
        Receive the next :class:`AudioFrame`.

        The base implementation just reads silence, subclass
        :class:`AudioStreamTrack` to provide a useful implementation.
        """
        sample_rate = 8000
        samples = int(AUDIO_PTIME * sample_rate)

        timestamp = getattr(self, '_timestamp', 0)
        self._timestamp = timestamp + samples
        await asyncio.sleep(AUDIO_PTIME)

        return AudioFrame(
            channels=1,
            data=b'\x00\x00' * samples,
            sample_rate=sample_rate,
            timestamp=timestamp)


class VideoStreamTrack(MediaStreamTrack):
    """
    A video stream track.
    """
    kind = 'video'

    async def next_timestamp(self):
        if hasattr(self, '_timestamp'):
            self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCKRATE)
            await asyncio.sleep(VIDEO_PTIME)
        else:
            self._timestamp = 0
        return self._timestamp

    async def recv(self):
        """
        Receive the next :class:`VideoFrame`.

        The base implementation just reads a 640x480 green frame at 30fps,
        subclass :class:`VideoStreamTrack` to provide a useful implementation.
        """
        timestamp = await self.next_timestamp()
        return VideoFrame(width=640, height=480, timestamp=timestamp)
