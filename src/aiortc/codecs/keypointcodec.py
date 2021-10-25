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
        keypoints_bytearray = encoded_frame.data
        keypoints = []
        for i in range(0, int(len(keypoints_bytearray)/4)):
            keypoint_x = int.from_bytes(keypoints_bytearray[4*i:4*i + 2], 'big')
            keypoint_y = int.from_bytes(keypoints_bytearray[4*i + 2:4*i + 4], 'big')
            keypoints.append([keypoint_x, keypoint_y])
        frame = KeypointsFrame(keypoints, encoded_frame.timestamp)
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
        timestamp = frame.pts
        keypoints = frame.data
        keypoints_bytearray = bytearray()
        for i in range(0, len(keypoints)):
            keypoint = keypoints[i]
            keypoint_x, keypoint_y = int(keypoint[0].item()), int(keypoint[1].item())
            keypoints_bytearray.extend((keypoint_x).to_bytes(2, 'big'))
            keypoints_bytearray.extend((keypoint_y).to_bytes(2, 'big'))
        data = keypoints_bytearray
        return [data], timestamp
