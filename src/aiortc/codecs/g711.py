import fractions
from typing import List, Tuple

from av import AudioFrame, CodecContext
from av.audio.codeccontext import AudioCodecContext
from av.audio.resampler import AudioResampler
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
    def __init__(self, codec_name: str) -> None:
        self.codec: AudioCodecContext = CodecContext.create(codec_name, "r")
        self.codec.format = "s16"
        self.codec.layout = "mono"
        self.codec.sample_rate = SAMPLE_RATE

    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        packet = Packet(encoded_frame.data)
        packet.pts = encoded_frame.timestamp
        packet.time_base = TIME_BASE
        return self.codec.decode(packet)


class PcmEncoder(Encoder):
    def __init__(self, codec_name: str) -> None:
        self.codec: AudioCodecContext = CodecContext.create(codec_name, "w")
        self.codec.format = "s16"
        self.codec.layout = "mono"
        self.codec.sample_rate = SAMPLE_RATE
        self.codec.time_base = TIME_BASE

        # Create our own resampler to control the frame size.
        self.resampler = AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE,
            frame_size=SAMPLES_PER_FRAME,
        )

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        assert isinstance(frame, AudioFrame)
        assert frame.format.name == "s16"
        assert frame.layout.name in ["mono", "stereo"]

        # Send frame through resampler and encoder.
        packets = []
        for frame in self.resampler.resample(frame):
            packets += self.codec.encode(frame)

        if packets:
            # Packets were returned.
            return [bytes(p) for p in packets], packets[0].pts
        else:
            # No packets were returned due to buffering.
            return [], None

    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, TIME_BASE)
        return [bytes(packet)], timestamp


class PcmaDecoder(PcmDecoder):
    def __init__(self) -> None:
        super().__init__("pcm_alaw")


class PcmaEncoder(PcmEncoder):
    def __init__(self) -> None:
        super().__init__("pcm_alaw")


class PcmuDecoder(PcmDecoder):
    def __init__(self) -> None:
        super().__init__("pcm_mulaw")


class PcmuEncoder(PcmEncoder):
    def __init__(self) -> None:
        super().__init__("pcm_mulaw")
