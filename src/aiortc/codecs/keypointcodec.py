# instead of random integer, each individual coordinate needs maximum 1-2 bytes because each coordiante is 0-256 range
import audioop
import fractions
from typing import List, Optional, Tuple

from ..jitterbuffer import JitterFrame
from .base import Decoder, Encoder

from ..mediastreams import KeypointsFrame

class KeypointsDecoder(Decoder): 
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        pass  # pragma: no cover

    def decode(self, encoded_frame: JitterFrame) -> List[KeypointsFrame]:
        print("Keypoints decoder is being called with input ", encoded_frame.data, encoded_frame.timestamp)
        frame = KeypointsFrame()
        frame.data = encoded_frame.data
        frame.pts = encoded_frame.timestamp
        print("Decoded frame in keypointcodec is", frame)
        return [frame]


class KeypointsEncoder(Encoder):
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        pass  # pragma: no cover

    def __init__(self) -> None:
        pass

    def encode(
        self, frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        print("Keypoints encoder is being called!")
        timestamp = frame.pts
        data = frame.data
        return [data], timestamp


