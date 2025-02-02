import fractions
from typing import List, Tuple

from av import AudioFrame, CodecContext
from av.audio.resampler import AudioResampler
from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame
from ..mediastreams import convert_timebase
from .base import Decoder, Encoder

CHANNELS = 2
SAMPLE_RATE = 48000
SAMPLE_WIDTH = 2
SAMPLES_PER_FRAME = 960
TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)


class OpusDecoder(Decoder):
    def __init__(self) -> None:
        self.decoder = CodecContext.create("libopus", "r")
        self.decoder.open()

    def __del__(self) -> None:
        self.decoder.close()

    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        frames = self.decoder.decode(Packet(encoded_frame.data))
        assert len(frames) == 1 and frames[0].samples == SAMPLES_PER_FRAME
        frames[0].pts = encoded_frame.timestamp
        frames[0].sample_rate = SAMPLE_RATE
        frames[0].time_base = TIME_BASE
        return frames


class OpusEncoder(Encoder):
    def __init__(self) -> None:
        codec = CodecContext.create("libopus", "w")
        codec.format = "s16"
        codec.layout = "stereo"
        codec.sample_rate = SAMPLE_RATE
        codec.bit_rate = 128000
        codec.time_base = TIME_BASE
        self.encoder = codec
        self.encoder.open()
        # Create our own resampler to control the frame size.
        self.resampler = AudioResampler(
            format="s16",
            layout="stereo",
            rate=SAMPLE_RATE,
            frame_size=SAMPLES_PER_FRAME,
        )

    def __del__(self) -> None:
        self.encoder.close()

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        assert isinstance(frame, AudioFrame)
        assert frame.format.name == "s16"
        assert frame.layout.name in ["mono", "stereo"]
        # Send frame through resampler and encoder.
        payloads = []
        timestamp = None
        for frame in self.resampler.resample(frame):
            for packet in self.encoder.encode(frame):
                payloads.append(bytes(packet))
            if timestamp is None:
                timestamp = frame.pts
        return payloads, timestamp

    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, TIME_BASE)
        return [bytes(packet)], timestamp
