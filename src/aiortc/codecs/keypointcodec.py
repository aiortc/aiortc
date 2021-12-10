import audioop
import fractions
import numpy as np
from typing import List, Optional, Tuple

from ..jitterbuffer import JitterFrame
from .base import Decoder, Encoder
from .keypoints_pb2 import KeypointInfo

from ..mediastreams import KeypointsFrame

""" custom codec that uses the protobuf module 
    to generically serialize and de-serialize 
    keypoints and associated information
    (might warrant further optimization, once 
    we settle on final data format)
"""

def keypoint_dict_to_struct(keypoint_dict):
    """ parse a keypoint dictionary form into a keypoint info structure """
    keypoint_info_struct = KeypointInfo()

    if 'keypoints' in keypoint_dict:
        for k in keypoint_dict['keypoints']:
            keypoint = keypoint_info_struct.keypoints.add()
            keypoint.xloc = k[0]
            keypoint.yloc = k[1]
    
    if 'jacobians' in keypoint_dict:
        for j in keypoint_dict['jacobians']:
            jacobian = keypoint_info_struct.jacobians.add()
            jacobian.d11 = j[0][0]
            jacobian.d12 = j[0][1]
            jacobian.d21 = j[1][0]
            jacobian.d22 = j[1][1]

    keypoint_info_struct.pts = keypoint_dict['pts']
    keypoint_info_struct.index = keypoint_dict['index']
    
    return keypoint_info_struct


def keypoint_struct_to_dict(keypoint_info_struct):
    """ parse a keypoint info structure into dictionary form """
    keypoint_dict = {}

    if len(keypoint_info_struct.keypoints) > 0:
        kp_array = []
        keypoints = keypoint_info_struct.keypoints
        for k in keypoints:
            kp_array.append(np.array([k.xloc, k.yloc]))
        keypoint_dict['keypoints'] = np.array(kp_array)
    
    if len(keypoint_info_struct.jacobians) > 0:
        jacobian_array = []
        jacobians = keypoint_info_struct.jacobians
        for j in jacobians:
            jacobian_array.append(np.array([[j.d11, j.d12], [j.d21, j.d22]]))
        keypoint_dict['jacobians'] = np.array(jacobian_array)

    keypoint_dict['pts'] = keypoint_info_struct.pts
    keypoint_dict['index'] = keypoint_info_struct.index

    return keypoint_dict


class KeypointsDecoder(Decoder): 
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        pass  # pragma: no cover

    def decode(self, encoded_frame: JitterFrame) -> List[KeypointsFrame]:
        keypoint_str = encoded_frame.data
        
        keypoint_info_struct = KeypointInfo()
        keypoint_info_struct.ParseFromString(keypoint_str)
        assert(keypoint_info_struct.IsInitialized())

        keypoint_dict = keypoint_struct_to_dict(keypoint_info_struct)
        frame = KeypointsFrame(keypoint_dict, keypoint_dict['pts'], keypoint_dict['index'])
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
        keypoint_dict = frame.data
        keypoint_dict['pts'] = frame.pts
        keypoint_dict['index'] = frame.index
        
        keypoint_info_struct = keypoint_dict_to_struct(keypoint_dict)
        assert(keypoint_info_struct.IsInitialized())

        data = keypoint_info_struct.SerializeToString()
        return [data], timestamp
