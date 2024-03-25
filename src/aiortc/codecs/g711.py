import fractions
from typing import List, Tuple

import av
from av import AudioFrame
from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame
from ..mediastreams import convert_timebase
from .base import Decoder, Encoder

SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2
SAMPLES_PER_FRAME = 160
TIME_BASE = fractions.Fraction(1, 8000)


class PcmDecoder(Decoder):
    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        if not hasattr(self, "codec"):
            self.codec = av.CodecContext.create(self.codec_name, "r")
            self.codec.layout = "mono"
            self.codec.sample_rate = SAMPLE_RATE

        packet = av.Packet(encoded_frame.data)
        packet.pts = encoded_frame.timestamp
        packet.time_base = TIME_BASE
        return self.codec.decode(packet)


class PcmEncoder(Encoder):
    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        assert isinstance(frame, AudioFrame)
        assert frame.format.name == "s16"
        assert frame.layout.name in ["mono", "stereo"]

        if not hasattr(self, "codec"):
            self.codec = av.Codec(self.codec_name, "w").create()
            self.codec.format = "s16"
            self.codec.layout = "mono"
            self.codec.sample_rate = SAMPLE_RATE
            self.codec.time_base = TIME_BASE

        packets = self.codec.encode(frame)
        assert len(packets) == 1

        return [bytes(packets[0])], packets[0].pts

    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, TIME_BASE)
        return [bytes(packet)], timestamp


class PcmaDecoder(PcmDecoder):
    codec_name = "pcm_alaw"


class PcmaEncoder(PcmEncoder):
    codec_name = "pcm_alaw"


class PcmuDecoder(PcmDecoder):
    codec_name = "pcm_mulaw"


class PcmuEncoder(PcmEncoder):
    codec_name = "pcm_mulaw"
