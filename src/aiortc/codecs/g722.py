import fractions
from typing import Optional, cast

from av import AudioCodecContext, AudioFrame, AudioResampler, CodecContext
from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame
from ..mediastreams import convert_timebase
from .base import Decoder, Encoder

SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
SAMPLES_PER_FRAME = 320
TIME_BASE = fractions.Fraction(1, 16000)

# Even though the sample rate is 16kHz, the clockrate is defined as 8kHz.
# This is why we have multiplications and divisions by 2 in the code.
CLOCK_BASE = fractions.Fraction(1, 8000)


class G722Decoder(Decoder):
    def __init__(self) -> None:
        self.codec = cast(AudioCodecContext, CodecContext.create("g722", "r"))
        self.codec.format = "s16"
        self.codec.layout = "mono"
        self.codec.sample_rate = SAMPLE_RATE

    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        packet = Packet(encoded_frame.data)
        packet.pts = encoded_frame.timestamp * 2
        packet.time_base = TIME_BASE
        return cast(list[Frame], self.codec.decode(packet))


class G722Encoder(Encoder):
    def __init__(self) -> None:
        self.codec = cast(AudioCodecContext, CodecContext.create("g722", "w"))
        self.codec.format = "s16"
        self.codec.layout = "mono"
        self.codec.sample_rate = SAMPLE_RATE
        self.codec.time_base = TIME_BASE
        self.first_pts: Optional[int] = None

        # Create our own resampler to control the frame size.
        self.resampler = AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE,
            frame_size=SAMPLES_PER_FRAME,
        )

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

        if packets:
            # Packets were returned.
            if self.first_pts is None:
                self.first_pts = packets[0].pts
            timestamp = (packets[0].pts - self.first_pts) // 2
            return [bytes(p) for p in packets], timestamp
        else:
            # No packets were returned due to buffering.
            return [], None

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, CLOCK_BASE)
        return [bytes(packet)], timestamp
