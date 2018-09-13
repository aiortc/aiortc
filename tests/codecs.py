from unittest import TestCase

from aiortc import AudioFrame, VideoFrame
from aiortc.codecs import get_decoder, get_encoder
from aiortc.rtp import RtpPacket

AUDIO_PTIME = 0.020


class CodecTestCase(TestCase):
    def roundtrip_audio(self, codec, output_channels, output_sample_rate):
        """
        Round-trip an AudioFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        # encode
        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000)
        self.assertEqual(len(frame.data), 320)
        data = encoder.encode(frame)

        # decode
        decoded = decoder.decode(data)
        self.assertEqual(len(decoded.data), output_sample_rate * AUDIO_PTIME * output_channels * 2)
        self.assertEqual(decoded.channels, output_channels)
        self.assertEqual(decoded.sample_rate, output_sample_rate)
        self.assertEqual(decoded.sample_width, 2)

    def roundtrip_video(self, codec, width, height):
        """
        Round-trip a VideoFrame through encoder then decoder.
        """
        encoder = get_encoder(codec)
        decoder = get_decoder(codec)

        # encode
        frame = VideoFrame(width=width, height=height)
        packages = encoder.encode(frame)

        # depacketize
        data = b''
        for package in packages:
            packet = RtpPacket(payload=package)
            decoder.parse(packet)
            data += packet._data

        # decode
        frames = decoder.decode(data)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].width, width)
        self.assertEqual(frames[0].height, height)
