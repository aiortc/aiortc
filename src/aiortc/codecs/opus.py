import fractions
from typing import Optional, cast

from av import AudioFrame, AudioResampler, CodecContext
from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame
from ..mediastreams import convert_timebase
from .base import Decoder, Encoder

SAMPLE_RATE = 48000
SAMPLES_PER_FRAME = 960
TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)


class OpusDecoder(Decoder):
    def __init__(self) -> None:
        self.codec = CodecContext.create("libopus", "r")
        self.codec.format = "s16"
        self.codec.layout = "stereo"
        self.codec.sample_rate = SAMPLE_RATE

    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        packet = Packet(encoded_frame.data)
        packet.pts = encoded_frame.timestamp
        packet.time_base = TIME_BASE
        return cast(list[Frame], self.codec.decode(packet))


class OpusEncoder(Encoder):
    def __init__(self) -> None:
        self.codec = CodecContext.create("libopus", "w")
        self.codec.bit_rate = 96000
        self.codec.format = "s16"
        self.codec.layout = "stereo"
        self.codec.options = {"application": "voip"}
        self.codec.sample_rate = SAMPLE_RATE
        self.codec.time_base = TIME_BASE

        # Create our own resampler to control the frame size.
        self.resampler = AudioResampler(
            format="s16",
            layout="stereo",
            rate=SAMPLE_RATE,
            frame_size=SAMPLES_PER_FRAME,
        )

        self.first_packet_pts: Optional[int] = None

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        assert isinstance(frame, AudioFrame)
        assert frame.format.name == "s16"
        assert frame.layout.name in ["mono", "stereo"]

        # Send frame through resampler and encoder.
        packets = []
        for frame in self.resampler.resample(frame):
            packets += self.codec.encode(frame)

        # For some reason the pts starts at a negative value,
        # so make a note of the first pts to cancel it out.
        if self.first_packet_pts is None and packets:
            self.first_packet_pts = packets[0].pts

        if packets:
            # Packets were returned.
            return [bytes(p) for p in packets], packets[0].pts - self.first_packet_pts
        else:
            # No packets were returned due to buffering.
            return [], None

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, TIME_BASE)
        return [bytes(packet)], timestamp
