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

SCALE_FACTOR = 256//2
NUM_KP = 10

""" compute the bin corresponding to the jacobian value
    based on the Huffman dictionary for the desired
    number of bins/bits
"""
def jacobian_to_bin(value, num_bins):
    sign = int(value > 0)
    value = abs(value)

    if value > 3:
        bin_num = num_bins - 1
    elif value > 2.5:
        bin_num = num_bins - 2
    elif value > 2:
        bin_num = num_bins - 3
    else:
        bin_num = int(value / 2.0 * (num_bins - 3))

    return sign, bin_num


""" compute the approximate jacobian from the bin number
    based on the Huffman dictionary for the desired
    number of bins/bits
"""
def bin_to_jacobian(bin_num, num_bins):
    if bin_num < num_bins - 3:
        num_intervals = num_bins - 3
        interval_size = 2.0
        return (interval_size / num_intervals) * (bin_num + 0.5)
    elif bin_num == num_bins - 3:
        value = 2.25
    elif bin_num == num_bins - 2:
        value = 2.75
    else:
        value = 3
    return value


""" custom encoding for keypoint data using lossless
    8-bit encoding for keypoint locations and lossy
    Huffman binning/encoding of jacobians
"""
def custom_encode(keypoint_dict):
    binary_str = ""
    num_bins = 2**(NUM_JACOBIAN_BITS - 1)
    bit_format = f'0{NUM_JACOBIAN_BITS - 1}b'
        
    for k in keypoint_dict['keypoints']:
        x = round(k[0] * SCALE_FACTOR + SCALE_FACTOR)
        y = round(k[1] * SCALE_FACTOR + SCLAE_FACTOR)
        binary_str += f'{x:08b}'
        binary_str += f'{y:08b}'

    for j in keypoint_dict['jacobians']:
        flattened_jacobians = j.flatten()
        for element in flattened_jacobians:
            sign, binary = jacobian_to_bin(element, num_bins)
            binary_str += f'{sign}{binary:{bit_format}}'

    return int(binary_str, 2).to_bytes(len(binary_str) // 8, byteorder='big')


""" custom decoding for keypoint data using lossless
    decoding for 8-bit keypoint locations and lossy
    decoding of jacobians based on Huffman bins
"""
def custom_decode(serialized_data):
    num_bins = 2**(NUM_JACOBIAN_BITS - 1)
    bitstring = ''.join(format(byte, '08b') for byte in serialized_data)

    keypoint_dict = {'jacobians': [], 'keypoints': []}
    num_read_so_far = 0
    x, y = 0, 0
    kp_locations = []
    jacobians = []

    while len(bitstring) > 0:
        num_bits = NUM_JACOBIAN_BITS if num_read_so_far >= 2*NUM_KP else 8
        word = bitstring[:num_bits]
        bitstring = bitstring[num_bits:]
        sign = -1 if word[0] == '0' else 1
        bin_number = int(word[1:num_bits], 2)
        num_read_so_far += 1

        if num_read_so_far <= 2 * NUM_KP:
            value = ((int(word, 2) - SCALE_FACTOR) / float(SCALE_FACTOR))
            if num_read_so_far % 2 == 0:
                kp_locations.append(value)
                keypoint_dict['keypoints'].append(np.array(kp_locations))
                kp_locations = []
            else:
                kp_locations.append(value)
        else:
            value = sign * bin_to_jacobian(bin_number, num_bins)
            if num_read_so_far % 4 == 0:
                jacobians.append(value)
                jacobians = np.array(jacobians).reshape((2, 2))
                keypoint_dict['jacobians'].append(jacobians)
                jacobians = []
            else:
                jacobians.append(value)

    keypoint_dict['jacobians'] = np.array(keypoint_dict['jacobians'])
    keypoint_dict['keypoints'] = np.array(keypoint_dict['keypoints'])
    return keypoint_dict


class KeypointsDecoder(Decoder): 
    @staticmethod
    def _convert(data: bytes, width: int) -> bytes:
        pass  # pragma: no cover

    def decode(self, encoded_frame: JitterFrame) -> List[KeypointsFrame]:
        keypoint_str = encoded_frame.data
        keypoint_dict = custom_decode(keypoint_str)
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
        
        data = custom_encode(keypoint_dict)
        return [data], timestamp
