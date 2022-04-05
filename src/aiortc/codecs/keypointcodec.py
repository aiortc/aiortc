import audioop
import fractions
import numpy as np
import os
from typing import List, Optional, Tuple

from ..jitterbuffer import JitterFrame
from .base import Decoder, Encoder
from .keypoints_pb2 import KeypointInfo
import bitstring
import math

from ..mediastreams import KeypointsFrame

NUM_KP = 10
FRAME_SIZE = int(os.environ.get('FRAME_SIZE', 1024))
NUM_JACOBIAN_BITS = 16
SCALE_FACTOR = FRAME_SIZE//2
FRAME_INDEX_BITS = 16
SRC_INDEX_BITS = 16
DUMMY_PTS = 5

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
    keypoint_info_struct.frame_index = keypoint_dict['frame_index']
    keypoint_info_struct.source_index = keypoint_dict['source_index']
    
    return keypoint_info_struct


def jacobian_to_float16(jacobian_val):
    """ convert jacobian to a 16 bit float value """
    bit_array = bitstring.BitArray(float=jacobian_val, length=32)
    bit_32 = bit_array.bin
    sign = bit_32[0]
    exponent = int(bit_32[1:9], 2) - 127 + 15
    mantissa = bit_32[9:][:10]

    binary_str = f'{sign}{exponent:05b}{mantissa}'
    return binary_str


def float16_to_jacobian(float16_bit_str):
    """ convert a float 16 value to jacobian """
    sign = float16_bit_str[0]
    exponent = int(float16_bit_str[1:6], 2) - 15 + 127
    mantissa = float16_bit_str[6:] + '0000000000000'
    float32_bitstring = f'0b{sign}{exponent:08b}{mantissa}'
    bit_array =  bitstring.BitArray(float32_bitstring)
    return bit_array.float


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
    keypoint_dict['frame_index'] = keypoint_info_struct.frame_index
    keypoint_dict['source_index'] = keypoint_info_struct.source_index

    return keypoint_dict


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
    num_bins = 2 ** (NUM_JACOBIAN_BITS - 1)
    bit_format = f'0{NUM_JACOBIAN_BITS - 1}b'

    # frame index
    index = keypoint_dict['frame_index']
    index_bit_format = f'0{FRAME_INDEX_BITS}b'
    binary_str += f'{index:{index_bit_format}}'

    # source frame index
    src_index = keypoint_dict['source_index']
    index_bit_format = f'0{SRC_INDEX_BITS}b'
    binary_str += f'{src_index:{index_bit_format}}'
        
    kp_bits = int(math.log(FRAME_SIZE, 2))
    for i, k in enumerate(keypoint_dict['keypoints']):
        x = min(round(k[0] * SCALE_FACTOR + SCALE_FACTOR), FRAME_SIZE - 1)
        y = min(round(k[1] * SCALE_FACTOR + SCALE_FACTOR), FRAME_SIZE - 1)
        binary_str += f'{x:0{kp_bits}b}'
        binary_str += f'{y:0{kp_bits}b}'

    for j in keypoint_dict['jacobians']:
        flattened_jacobians = j.flatten()
        for element in flattened_jacobians:
            binary_str += jacobian_to_float16(element)

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

    # frame index
    index = int(bitstring[:FRAME_INDEX_BITS], 2)
    keypoint_dict['frame_index'] = index
    bitstring = bitstring[FRAME_INDEX_BITS:]
    
    # source frame index to reconstruct from
    source_index = int(bitstring[:SRC_INDEX_BITS], 2)
    keypoint_dict['source_index'] = source_index
    bitstring = bitstring[SRC_INDEX_BITS:]
    
    kp_bits = int(math.log(FRAME_SIZE, 2))
    while len(bitstring) > 0:
        num_bits = 16 if num_read_so_far >= 2*NUM_KP else kp_bits
        word = bitstring[:num_bits]
        bitstring = bitstring[num_bits:]
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
            value = float16_to_jacobian(word)
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

        if NUM_JACOBIAN_BITS == -1:
            keypoint_info_struct = KeypointInfo()
            keypoint_info_struct.ParseFromString(keypoint_str)
            assert(keypoint_info_struct.IsInitialized())
            keypoint_dict = keypoint_struct_to_dict(keypoint_info_struct)
        else:
            keypoint_dict = custom_decode(keypoint_str)
            keypoint_dict['pts'] = DUMMY_PTS
        
        frame = KeypointsFrame(keypoint_dict, keypoint_dict['pts'], \
                keypoint_dict['frame_index'], keypoint_dict['source_index'])
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
        keypoint_dict['frame_index'] = frame.frame_index
        keypoint_dict['source_index'] = frame.source_index

        if NUM_JACOBIAN_BITS == -1:
            keypoint_info_struct = keypoint_dict_to_struct(keypoint_dict)
            assert(keypoint_info_struct.IsInitialized())
            data = keypoint_info_struct.SerializeToString()
        else:
            data = custom_encode(keypoint_dict)

        return [data], timestamp
