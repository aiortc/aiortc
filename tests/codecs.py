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
                data=b'\x00\x00' * channels * samples_per_frame,
                sample_rate=sample_rate)
            frame.pts = timestamp
            frame.time_base = fractions.Fraction(1, sample_rate)
            frames.append(frame)
            timestamp += samples_per_frame
        return frames

    def roundtrip_audio(self, codec, output_channels, output_sample_rate):
        """
        Round-trip an AudioFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        input_sample_rate = 8000
        input_sample_count = int(input_sample_rate * AUDIO_PTIME)
        input_timestamp = 0

        output_sample_count = int(output_sample_rate * AUDIO_PTIME)
        output_timestamp = 0

        for frame in self.create_audio_frames(channels=1, sample_rate=input_sample_rate, count=10):
            # encode
            self.assertEqual(len(frame.data), 320)
            packages, timestamp = encoder.encode(frame)

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
            self.assertEqual(frames[0].pts, output_timestamp)
            self.assertEqual(frames[0].time_base, fractions.Fraction(1, output_sample_rate))

            # tick
            input_timestamp += input_sample_count
            output_timestamp += output_sample_count

    def roundtrip_video(self, codec, width, height):
        """
        Round-trip a VideoFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        for timestamp in range(0, 90000, 3000):
            # encode
            frame = VideoFrame(width=width, height=height)
            frame.pts = timestamp
            frame.time_base = VIDEO_TIME_BASE
            packages, timestamp = encoder.encode(frame)

            # depacketize
            data = b''
            for package in packages:
                data += depayload(codec, package)

            # decode
            frames = decoder.decode(JitterFrame(data=data, timestamp=timestamp))
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].width, width)
            self.assertEqual(frames[0].height, height)
            self.assertEqual(frames[0].pts, timestamp)
            self.assertEqual(frames[0].time_base, VIDEO_TIME_BASE)
