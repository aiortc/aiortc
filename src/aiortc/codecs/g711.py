import audioop
import fractions
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

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


class PcmDecoder(ABC, Decoder):
    @staticmethod
    @abstractmethod
    def _convert(data: bytes, width: int) -> bytes:
        pass  # pragma: no cover

    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        frame = AudioFrame(format="s16", layout="mono", samples=SAMPLES_PER_FRAME)
        frame.planes[0].update(self._convert(encoded_frame.data, SAMPLE_WIDTH))
        frame.pts = encoded_frame.timestamp
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = TIME_BASE
        return [frame]


class PcmEncoder(ABC, Encoder):
    @staticmethod
    @abstractmethod
    def _convert(data: bytes, width: int) -> bytes:
        pass  # pragma: no cover

    def __init__(self) -> None:
        self.rate_state: Optional[Tuple[int, Tuple[Tuple[int, int], ...]]] = None

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        assert isinstance(frame, AudioFrame)
        assert frame.format.name == "s16"
        assert frame.layout.name in ["mono", "stereo"]

        channels = len(frame.layout.channels)
        data = bytes(frame.planes[0])
        timestamp = frame.pts

        # resample at 8 kHz
        if frame.sample_rate != SAMPLE_RATE:
            data, self.rate_state = audioop.ratecv(
                data,
                SAMPLE_WIDTH,
                channels,
                frame.sample_rate,
                SAMPLE_RATE,
                self.rate_state,
            )
            timestamp = (timestamp * SAMPLE_RATE) // frame.sample_rate

        # convert to mono
        if channels == 2:
            data = audioop.tomono(data, SAMPLE_WIDTH, 1, 1)

        data = self._convert(data, SAMPLE_WIDTH)
        return [data], timestamp

    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
        timestamp = convert_timebase(packet.pts, packet.time_base, TIME_BASE)
        return [bytes(packet)], timestamp


class PcmaDecoder(PcmDecoder):
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        return audioop.alaw2lin(data, width)


class PcmaEncoder(PcmEncoder):
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        return audioop.lin2alaw(data, width)


class PcmuDecoder(PcmDecoder):
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        return audioop.ulaw2lin(data, width)


class PcmuEncoder(PcmEncoder):
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        return audioop.lin2ulaw(data, width)
