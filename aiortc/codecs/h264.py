import fractions
import logging
import math
from itertools import tee
from struct import pack, unpack_from

import av

from ..mediastreams import VIDEO_TIME_BASE, convert_timebase

logger = logging.getLogger('codec.h264')

MAX_FRAME_RATE = 30
PACKET_MAX = 1300

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

    def __repr__(self):
        return 'H264PayloadDescriptor(FF={})'.format(self.first_fragment)

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
        self.codec = av.CodecContext.create('h264', 'r')

    def decode(self, encoded_frame):
        try:
            packet = av.packet.Packet(encoded_frame.data)
            packet.pts = encoded_frame.timestamp
            packet.time_base = VIDEO_TIME_BASE
            frames = self.codec.decode(packet)
        except av.AVError as e:
            logger.warning('failed to decode, skipping package: ' + str(e))
            return []

        return frames


class H264Encoder:
    def __init__(self):
        self.codec = None

    @staticmethod
    def _packetize_fu_a(data):
        available_size = PACKET_MAX - FU_A_HEADER_SIZE
        payload_size = len(data) - NAL_HEADER_SIZE
        num_packets = math.ceil(payload_size / available_size)
        num_larger_packets = payload_size % num_packets
        package_size = payload_size // num_packets

        f_nri = data[0] & (0x80 | 0x60)  # fni of original header
        nal = data[0] & 0x1f

        fu_indicator = f_nri | NAL_TYPE_FU_A

        fu_header_end = bytes([fu_indicator, nal | 0x40])
        fu_header_middle = bytes([fu_indicator, nal])
        fu_header_start = bytes([fu_indicator, nal | 0x80])
        fu_header = fu_header_start

        packages = []
        offset = NAL_HEADER_SIZE
        while offset < len(data):
            if num_larger_packets > 0:
                num_larger_packets -= 1
                payload = data[offset:offset+package_size+1]
                offset += package_size+1
            else:
                payload = data[offset:offset+package_size]
                offset += package_size

            if offset == len(data):
                fu_header = fu_header_end

            packages.append(fu_header + payload)

            fu_header = fu_header_middle
        assert offset == len(data), 'incorrect fragment data'

        return packages

    @staticmethod
    def _packetize_stap_a(data, packages_iterator):
        counter = 0
        available_size = PACKET_MAX - STAP_A_HEADER_SIZE

        stap_header = NAL_TYPE_STAP_A | (data[0] & 0xe0)

        payload = bytes()
        try:
            nalu = data  # with header
            while len(nalu) <= available_size:
                stap_header |= nalu[0] & 0x80

                nri = nalu[0] & 0x60
                if stap_header & 0x60 < nri:
                    stap_header = (stap_header & 0x9f | nri)

                available_size -= LENGTH_FIELD_SIZE + len(nalu)
                counter += 1
                payload += pack('!H', len(nalu)) + nalu
                nalu = next(packages_iterator)

            if counter == 0:
                nalu = next(packages_iterator)
        except StopIteration:
            nalu = None

        if counter <= 1:
            return data, nalu
        else:
            return bytes([stap_header]) + payload, nalu

    @staticmethod
    def _split_bitstream(buf):
        # TODO: write in a more pytonic way,
        # translate from: https://github.com/aizvorski/h264bitstream/blob/master/h264_nal.c#L134
        i = 0
        while True:
            while ((buf[i] != 0 or buf[i+1] != 0 or buf[i+2] != 0x01)
                    and (buf[i] != 0 or buf[i+1] != 0 or buf[i+2] != 0 or buf[i+3] != 0x01)):
                i += 1  # skip leading zero
                if i+4 >= len(buf):
                    return
            if buf[i] != 0 or buf[i+1] != 0 or buf[i+2] != 0x01:
                i += 1
            i += 3
            nal_start = i
            while ((buf[i] != 0 or buf[i+1] != 0 or buf[i+2] != 0)
                    and (buf[i] != 0 or buf[i+1] != 0 or buf[i+2] != 0x01)):
                i += 1
                # FIXME: the next line fails when reading a nal that ends
                # exactly at the end of the data
                if i+3 >= len(buf):
                    nal_end = len(buf)
                    yield buf[nal_start:nal_end]
                    return  # did not find nal end, stream ended first
            nal_end = i
            yield buf[nal_start:nal_end]

    @classmethod
    def _packetize(cls, packages):
        packetized_packages = []

        packages_iterator = iter(packages)
        package = next(packages_iterator, None)
        while package is not None:
            if len(package) > PACKET_MAX:
                packetized_packages.extend(cls._packetize_fu_a(package))
                package = next(packages_iterator, None)
            else:
                packetized, package = cls._packetize_stap_a(package, packages_iterator)
                packetized_packages.append(packetized)

        return packetized_packages

    def _encode_frame(self, frame, force_keyframe):
        if self.codec and (frame.width != self.codec.width or frame.height != self.codec.height):
            self.codec = None

        if self.codec is None:
            self.codec = av.CodecContext.create('libx264', 'w')
            self.codec.width = frame.width
            self.codec.height = frame.height
            self.codec.pix_fmt = 'yuv420p'
            self.codec.time_base = fractions.Fraction(1, MAX_FRAME_RATE)
            self.codec.options = {
                'profile': 'baseline',
                'level': '31',
                'tune': 'zerolatency'
            }

        packages = self.codec.encode(frame)
        yield from self._split_bitstream(b''.join(p.to_bytes() for p in packages))

    def encode(self, frame, force_keyframe=False):
        packages = self._encode_frame(frame, force_keyframe)
        timestamp = convert_timebase(frame.pts, frame.time_base, VIDEO_TIME_BASE)
        return self._packetize(packages), timestamp


def h264_depayload(payload):
    descriptor, data = H264PayloadDescriptor.parse(payload)
    return data
