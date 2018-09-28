from unittest import TestCase

from aiortc import AudioFrame, VideoFrame
from aiortc.codecs import depayload, get_decoder, get_encoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import AUDIO_PTIME


class CodecTestCase(TestCase):
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

        for i in range(10):
            # encode
            frame = AudioFrame(
                channels=1,
                data=b'\x00\x00' * input_sample_count,
                sample_rate=input_sample_rate,
                timestamp=input_timestamp)
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
            self.assertEqual(frames[0].timestamp, output_timestamp)

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
            frame = VideoFrame(width=width, height=height, timestamp=timestamp)
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
            self.assertEqual(frames[0].timestamp, timestamp)
