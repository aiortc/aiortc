import fractions
from unittest import TestCase

from aiortc import AudioFrame, VideoFrame
from aiortc.codecs import depayload, get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import AUDIO_PTIME, VIDEO_TIME_BASE


class CodecTestCase(TestCase):
    def create_audio_frames(self, channels, sample_rate, count):
        frames = []
        timestamp = 0
        samples_per_frame = int(AUDIO_PTIME * sample_rate)
        for i in range(count):
            frame = AudioFrame(
                channels=channels,
                data=bytes(2 * channels * samples_per_frame),
                sample_rate=sample_rate)
            frame.pts = timestamp
            frame.time_base = fractions.Fraction(1, sample_rate)
            frames.append(frame)
            timestamp += samples_per_frame
        return frames

    def create_video_frames(self, width, height, count, time_base=VIDEO_TIME_BASE):
        frames = []
        for i in range(count):
            frame = VideoFrame(width=width, height=height)
            frame.pts = int(i / time_base / 30)
            frame.time_base = time_base
            frames.append(frame)
        return frames

    def roundtrip_audio(self, codec, output_channels, output_sample_rate, drop=[]):
        """
        Round-trip an AudioFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_frames = self.create_audio_frames(channels=1, sample_rate=8000, count=10)

        output_sample_count = int(output_sample_rate * AUDIO_PTIME)

        for i, frame in enumerate(input_frames):
            # encode
            self.assertEqual(len(frame.data), 320)
            packages, timestamp = encoder.encode(frame)

            if i not in drop:
                # depacketize
                data = b''
                for package in packages:
                    data += depayload(codec, package)

                # decode
                frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
                self.assertEqual(len(frames), 1)
                self.assertEqual(len(frames[0].data),
                                 output_sample_rate * AUDIO_PTIME * output_channels * 2)
                self.assertEqual(frames[0].channels, output_channels)
                self.assertEqual(frames[0].sample_rate, output_sample_rate)
                self.assertEqual(frames[0].sample_width, 2)
                self.assertEqual(frames[0].pts, i * output_sample_count)
                self.assertEqual(frames[0].time_base, fractions.Fraction(1, output_sample_rate))

    def roundtrip_video(self, codec, width, height, time_base=VIDEO_TIME_BASE):
        """
        Round-trip a VideoFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_frames = self.create_video_frames(width=width, height=height, count=30, time_base=time_base)
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
