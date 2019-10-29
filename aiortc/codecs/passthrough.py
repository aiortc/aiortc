import logging

from . import H264Encoder

logger = logging.getLogger("codec.passthrough")

'''
This encoder should be used for encoded h264 or vp8 video stream only
It passes the input directly to the rtcrtpsender to avoid unnecessary encoding & decoding
'''


class PassThroughEncoder:

    # def _encode_frame(self, packages):
    #     yield from H264Encoder._sp_split_bitstream(b"".join(p.to_bytes() for p in packages))

    @staticmethod
    def encode(self, frame, timestamp):
        # packages = self._encode_frame(frame)
        print("ps encode called")
        return H264Encoder._packetize(frame), timestamp
