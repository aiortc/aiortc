import fractions
from unittest import TestCase

from aiortc.codecs import depayload, get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import AUDIO_PTIME, VIDEO_TIME_BASE
from av import AudioFrame, VideoFrame
from av.packet import Packet


class CodecTestCase(TestCase):
    def create_audio_frame(
        self, samples: int, pts: int, layout="mono", sample_rate=48000
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
        format="yuv420p",
        time_base=VIDEO_TIME_BASE,
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
        self, width: int, height: int, count: int, time_base=VIDEO_TIME_BASE
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
        codec,
        output_layout,
        output_sample_rate,
        input_layout="mono",
        input_sample_rate=8000,
        drop=[],
    ):
        """
        Round-trip an AudioFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_frames = self.create_audio_frames(
            layout=input_layout, sample_rate=input_sample_rate, count=10
        )

        output_sample_count = int(output_sample_rate * AUDIO_PTIME)

        for i, frame in enumerate(input_frames):
            # encode
            packages, timestamp = encoder.encode(frame)

            if i not in drop:
                # depacketize
                data = b""
                for package in packages:
                    data += depayload(codec, package)

                # decode
                frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0].format.name, "s16")
                self.assertEqual(frames[0].layout.name, output_layout)
                self.assertEqual(frames[0].samples, output_sample_rate * AUDIO_PTIME)
                self.assertEqual(frames[0].sample_rate, output_sample_rate)
                self.assertEqual(frames[0].pts, i * output_sample_count)
                self.assertEqual(
                    frames[0].time_base, fractions.Fraction(1, output_sample_rate)
                )

    def roundtrip_video(self, codec, width, height, time_base=VIDEO_TIME_BASE):
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
            self.assertEqual(frames[0].width, frame.width)
            self.assertEqual(frames[0].height, frame.height)
            self.assertAlmostEqual(frames[0].pts, i * 3000, delta=1)
            self.assertEqual(frames[0].time_base, VIDEO_TIME_BASE)
