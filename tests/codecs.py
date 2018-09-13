from aiortc import VideoFrame
from aiortc.codecs import get_decoder, get_encoder
from aiortc.rtp import RtpPacket


class CodecTestMixin:
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
