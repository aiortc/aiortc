import fractions
from unittest import TestCase

from av import AudioFrame, VideoFrame

from aiortc.codecs import depayload, get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import AUDIO_PTIME, VIDEO_TIME_BASE


class CodecTestCase(TestCase):
    def create_audio_frames(self, layout, sample_rate, count):
        frames = []
        timestamp = 0
        samples_per_frame = int(AUDIO_PTIME * sample_rate)
        for i in range(count):
            frame = AudioFrame(format='s16', layout=layout, samples=samples_per_frame)
            for p in frame.planes:
                p.update(bytes(p.buffer_size))
            frame.pts = timestamp
            frame.sample_rate = sample_rate
            frame.time_base = fractions.Fraction(1, sample_rate)
            frames.append(frame)
            timestamp += samples_per_frame
        return frames

    def create_video_frame(self, width, height, pts, format='yuv420p', time_base=VIDEO_TIME_BASE):
        """
        Create a single blank video frame.
        """
        frame = VideoFrame(width=width, height=height, format=format)
        for p in frame.planes:
            p.update(bytes(p.buffer_size))
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def create_video_frames(self, width, height, count, time_base=VIDEO_TIME_BASE):
        """
        Create consecutive blank video frames.
        """
        frames = []
        for i in range(count):
            frames.append(self.create_video_frame(
                width=width,
                height=height,
                pts=int(i / time_base / 30),
                time_base=time_base))
        return frames

    def roundtrip_audio(self, codec, output_layout, output_sample_rate, drop=[]):
        """
        Round-trip an AudioFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_frames = self.create_audio_frames(layout='mono', sample_rate=8000, count=10)

        output_sample_count = int(output_sample_rate * AUDIO_PTIME)

        for i, frame in enumerate(input_frames):
            # encode
            packages, timestamp = encoder.encode(frame)

            if i not in drop:
                # depacketize
                data = b''
                for package in packages:
                    data += depayload(codec, package)

                # decode
                frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0].format.name, 's16')
                self.assertEqual(frames[0].layout.name, output_layout)
                self.assertEqual(frames[0].samples, output_sample_rate * AUDIO_PTIME)
                self.assertEqual(frames[0].sample_rate, output_sample_rate)
                self.assertEqual(frames[0].pts, i * output_sample_count)
                self.assertEqual(frames[0].time_base, fractions.Fraction(1, output_sample_rate))

    def roundtrip_video(self, codec, width, height, time_base=VIDEO_TIME_BASE):
        """
        Round-trip a VideoFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_frames = self.create_video_frames(
            width=width, height=height, count=30, time_base=time_base)
        for i, frame in enumerate(input_frames):
            # encode
            packages, timestamp = encoder.encode(frame)

            # depacketize
            data = b''
            for package in packages:
                data += depayload(codec, package)

            # decode
            frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].width, frame.width)
            self.assertEqual(frames[0].height, frame.height)
            self.assertEqual(frames[0].pts, i * 3000)
            self.assertEqual(frames[0].time_base, VIDEO_TIME_BASE)
