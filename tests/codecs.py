import fractions
from typing import Optional

from aiortc.codecs import depayload, get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import AUDIO_PTIME, VIDEO_TIME_BASE
from aiortc.rtcrtpparameters import RTCRtpCodecParameters
from av import AudioFrame, VideoFrame
from av.frame import Frame
from av.packet import Packet

from .utils import TestCase


class CodecTestCase(TestCase):
    def assertAudioFrame(
        self,
        frame: Frame,
        *,
        layout: str,
        pts: int,
        samples: int,
        sample_rate: int,
        data: Optional[bytes],
    ) -> None:
        assert isinstance(frame, AudioFrame)
        self.assertEqual(frame.format.name, "s16")
        self.assertEqual(frame.layout.name, layout)
        self.assertEqual(frame.pts, pts)
        self.assertEqual(frame.samples, samples)
        self.assertEqual(frame.sample_rate, sample_rate)
        self.assertEqual(frame.time_base, fractions.Fraction(1, sample_rate))

        if data is not None:
            plane_data = bytes(frame.planes[0])
            self.assertEqual(plane_data[: len(data)], data)

    def create_audio_frame(
        self, samples: int, pts: int, layout: str = "mono", sample_rate: int = 48000
    ) -> AudioFrame:
        frame = AudioFrame(format="s16", layout=layout, samples=samples)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.sample_rate = sample_rate
        frame.time_base = fractions.Fraction(1, sample_rate)
        return frame

    def create_audio_frames(
        self, layout: str, sample_rate: int, count: int
    ) -> list[AudioFrame]:
        frames = []
        timestamp = 0
        samples_per_frame = int(AUDIO_PTIME * sample_rate)
        for i in range(count):
            frames.append(
                self.create_audio_frame(
                    samples=samples_per_frame,
                    pts=timestamp,
                    layout=layout,
                    sample_rate=sample_rate,
                )
            )
            timestamp += samples_per_frame
        return frames

    def create_packet(self, payload: bytes, pts: int) -> Packet:
        """
        Create a packet.
        """
        packet = Packet(len(payload))
        packet.update(payload)
        packet.pts = pts
        packet.time_base = fractions.Fraction(1, 1000)
        return packet

    def create_video_frame(
        self,
        width: int,
        height: int,
        pts: int,
        format: str = "yuv420p",
        time_base: fractions.Fraction = VIDEO_TIME_BASE,
    ) -> VideoFrame:
        """
        Create a single blank video frame.
        """
        frame = VideoFrame(width=width, height=height, format=format)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def create_video_frames(
        self,
        width: int,
        height: int,
        count: int,
        time_base: fractions.Fraction = VIDEO_TIME_BASE,
    ) -> list[VideoFrame]:
        """
        Create consecutive blank video frames.
        """
        frames = []
        for i in range(count):
            frames.append(
                self.create_video_frame(
                    width=width,
                    height=height,
                    pts=int(i / time_base / 30),
                    time_base=time_base,
                )
            )
        return frames

    def roundtrip_audio(
        self,
        codec: RTCRtpCodecParameters,
        layout: str,
        sample_rate: int,
        drop: list[int] = [],
    ) -> None:
        """
        Round-trip an AudioFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        samples = int(sample_rate * AUDIO_PTIME)
        time_base = fractions.Fraction(1, sample_rate)

        input_frames = self.create_audio_frames(
            layout=layout, sample_rate=sample_rate, count=10
        )
        for i, frame in enumerate(input_frames):
            # encode
            packages, timestamp = encoder.encode(frame)
            self.assertEqual(timestamp, i * codec.clockRate * AUDIO_PTIME)

            if i not in drop:
                # depacketize
                data = b""
                for package in packages:
                    data += depayload(codec, package)

                # decode
                frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
                self.assertEqual(len(frames), 1)
                assert isinstance(frames[0], AudioFrame)
                self.assertEqual(frames[0].format.name, "s16")
                self.assertEqual(frames[0].layout.name, layout)
                self.assertEqual(frames[0].samples, samples)
                self.assertEqual(frames[0].sample_rate, sample_rate)
                self.assertEqual(frames[0].pts, i * samples)
                self.assertEqual(frames[0].time_base, time_base)

    def roundtrip_video(
        self,
        codec: RTCRtpCodecParameters,
        width: int,
        height: int,
        time_base: fractions.Fraction = VIDEO_TIME_BASE,
    ) -> None:
        """
        Round-trip a VideoFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_frames = self.create_video_frames(
            width=width, height=height, count=30, time_base=time_base
        )
        for i, frame in enumerate(input_frames):
            # encode
            packages, timestamp = encoder.encode(frame)

            # depacketize
            data = b""
            for package in packages:
                data += depayload(codec, package)

            # decode
            frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
            self.assertEqual(len(frames), 1)
            assert isinstance(frames[0], VideoFrame)
            self.assertEqual(frames[0].width, frame.width)
            self.assertEqual(frames[0].height, frame.height)
            self.assertAlmostEqual(frames[0].pts, i * 3000, delta=1)
            self.assertEqual(frames[0].time_base, VIDEO_TIME_BASE)
