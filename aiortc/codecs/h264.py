from itertools import tee
from struct import unpack_from

from av.packet import Packet
from av.video.frame import VideoFrame
from av.codec.context import CodecContext

from ..contrib.media import frame_from_bgr, frame_to_bgr


PACKET_MAX = 1300 - 4
MAX_FRAME_RATE = 30

NAL_TYPE_FU_A = 28
NAL_TYPE_STAP_A = 24

NAL_HEADER_SIZE = 1
FU_A_HEADER_SIZE = 2
LENGTH_FIELD_SIZE = 2
STAP_A_HEADER_SIZE = NAL_HEADER_SIZE + LENGTH_FIELD_SIZE


def pairwise(iterable):
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)


class H264PayloadDescriptor:
    def __init__(self, first_fragment):
        self.first_fragment = first_fragment

    def __bytes__(self):
        return bytes()

    def __repr__(self):
        return 'H264PayloadDescriptor()'

    @classmethod
    def parse(cls, data):
        output = bytes()

        nal_type = data[0] & 0x1f
        if nal_type == NAL_TYPE_FU_A:
            assert len(data) >= FU_A_HEADER_SIZE, 'FU-A NAL units truncated.'

            f_nri = data[0] & (0x80 | 0x60)
            original_nal_type = data[1] & 0x1f
            first_fragment = bool(data[1] & 0x80)

            if first_fragment:
                original_nal_header = bytes([f_nri | original_nal_type])
                output += bytes([0, 0, 0, 1])
                output += original_nal_header

            output += data[2:]

            obj = cls(first_fragment=first_fragment)
        else:
            offsets = []
            if nal_type == NAL_TYPE_STAP_A:
                assert len(data) > STAP_A_HEADER_SIZE,  'StapA header truncated.'

                offset = 1
                while offset < len(data):
                    (nulu_size,) = unpack_from('!H', data, offset)
                    offset += LENGTH_FIELD_SIZE
                    assert offset < len(data), 'StapA length field truncated.'
                    offsets.append(offset)
                    offset += nulu_size
                    assert offset <= len(data), 'StapA packet with incorrect NALU packet lengths.'

                nal_type = data[STAP_A_HEADER_SIZE] & 0x1f
            else:
                offsets.append(0)

            offsets.append(len(data) + LENGTH_FIELD_SIZE)
            for start, end in pairwise(offsets):
                end -= LENGTH_FIELD_SIZE
                output += bytes([0, 0, 0, 1])
                output += data[start:end]

            obj = cls(first_fragment=True)

        return obj, output


class H264Decoder:
    def __init__(self):
        self.codec = CodecContext.create('h264', 'r')

    def decode(self, data):
        packet = Packet(data)
        frames = self.codec.decode(packet)

        video_frames = []
        for frame in frames:
            # TODO: avoid convert twice
            bgr_frame = frame.to_nd_array(format='bgr24')
            video_frame = frame_from_bgr(bgr_frame)

            video_frames.append(video_frame)

        return video_frames

    def parse(self, packet):
        descriptor, data = H264PayloadDescriptor.parse(packet.payload)
        packet._data = data
        packet._first_in_frame = descriptor.first_fragment


class H264Encoder:
    timestamp_increment = 3000

    def __init__(self):
        self.codec = CodecContext.create('libx264', 'w')
        self.codec.width = 320
        self.codec.height = 240
        self.codec.pix_fmt = 'yuv420p'

    def encode(self, frame, force_keyframe=False):
        bgr_frame = frame_to_bgr(frame)
        av_frame = VideoFrame.from_ndarray(bgr_frame, 'bgr24')
        packages = self.codec.encode(av_frame)

        # WIP: Implement Packetizer
        return []  # [p.to_bytes() for p in packages]
