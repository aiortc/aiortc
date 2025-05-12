import fractions
import logging
import math
from collections.abc import Iterable, Iterator, Sequence
from itertools import tee
from struct import pack, unpack_from
from typing import Optional, Type, TypeVar, cast

import av
from av.frame import Frame
from av.packet import Packet
from av.video.codeccontext import VideoCodecContext

from ..jitterbuffer import JitterFrame
from ..mediastreams import VIDEO_TIME_BASE, convert_timebase
from .base import Decoder, Encoder

logger = logging.getLogger(__name__)

DEFAULT_BITRATE = 1000000  # 1 Mbps
MIN_BITRATE = 500000  # 500 kbps
MAX_BITRATE = 3000000  # 3 Mbps

MAX_FRAME_RATE = 30
PACKET_MAX = 1300

NAL_TYPE_FU_A = 28
NAL_TYPE_STAP_A = 24

NAL_HEADER_SIZE = 1
FU_A_HEADER_SIZE = 2
LENGTH_FIELD_SIZE = 2
STAP_A_HEADER_SIZE = NAL_HEADER_SIZE + LENGTH_FIELD_SIZE

DESCRIPTOR_T = TypeVar("DESCRIPTOR_T", bound="H264PayloadDescriptor")
T = TypeVar("T")


def pairwise(iterable: Sequence[T]) -> Iterator[tuple[T, T]]:
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)


class H264PayloadDescriptor:
    def __init__(self, first_fragment: bool) -> None:
        self.first_fragment = first_fragment

    def __repr__(self) -> str:
        return f"H264PayloadDescriptor(FF={self.first_fragment})"

    @classmethod
    def parse(cls: Type[DESCRIPTOR_T], data: bytes) -> tuple[DESCRIPTOR_T, bytes]:
        output = bytes()

        # NAL unit header
        if len(data) < 2:
            raise ValueError("NAL unit is too short")
        nal_type = data[0] & 0x1F
        f_nri = data[0] & (0x80 | 0x60)
        pos = NAL_HEADER_SIZE

        if nal_type in range(1, 24):
            # single NAL unit
            output = bytes([0, 0, 0, 1]) + data
            obj = cls(first_fragment=True)
        elif nal_type == NAL_TYPE_FU_A:
            # fragmentation unit
            original_nal_type = data[pos] & 0x1F
            first_fragment = bool(data[pos] & 0x80)
            pos += 1

            if first_fragment:
                original_nal_header = bytes([f_nri | original_nal_type])
                output += bytes([0, 0, 0, 1])
                output += original_nal_header
            output += data[pos:]

            obj = cls(first_fragment=first_fragment)
        elif nal_type == NAL_TYPE_STAP_A:
            # single time aggregation packet
            offsets = []
            while pos < len(data):
                if len(data) < pos + LENGTH_FIELD_SIZE:
                    raise ValueError("STAP-A length field is truncated")
                nalu_size = unpack_from("!H", data, pos)[0]
                pos += LENGTH_FIELD_SIZE
                offsets.append(pos)

                pos += nalu_size
                if len(data) < pos:
                    raise ValueError("STAP-A data is truncated")

            offsets.append(len(data) + LENGTH_FIELD_SIZE)
            for start, end in pairwise(offsets):
                end -= LENGTH_FIELD_SIZE
                output += bytes([0, 0, 0, 1])
                output += data[start:end]

            obj = cls(first_fragment=True)
        else:
            raise ValueError(f"NAL unit type {nal_type} is not supported")

        return obj, output


class H264Decoder(Decoder):
    def __init__(self) -> None:
        self.codec = av.CodecContext.create("h264", "r")

    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        try:
            packet = av.Packet(encoded_frame.data)
            packet.pts = encoded_frame.timestamp
            packet.time_base = VIDEO_TIME_BASE
            return cast(list[Frame], self.codec.decode(packet))
        except av.FFmpegError as e:
            logger.warning(
                "H264Decoder() failed to decode, skipping package: " + str(e)
            )
            return []


class H264Encoder(Encoder):
    def __init__(self) -> None:
        self.buffer_data = b""
        self.buffer_pts: Optional[int] = None
        self.codec: Optional[VideoCodecContext] = None
        self.__target_bitrate = DEFAULT_BITRATE

    @staticmethod
    def _packetize_fu_a(data: bytes) -> list[bytes]:
        available_size = PACKET_MAX - FU_A_HEADER_SIZE
        payload_size = len(data) - NAL_HEADER_SIZE
        num_packets = math.ceil(payload_size / available_size)
        num_larger_packets = payload_size % num_packets
        package_size = payload_size // num_packets

        f_nri = data[0] & (0x80 | 0x60)  # fni of original header
        nal = data[0] & 0x1F

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
                payload = data[offset : offset + package_size + 1]
                offset += package_size + 1
            else:
                payload = data[offset : offset + package_size]
                offset += package_size

            if offset == len(data):
                fu_header = fu_header_end

            packages.append(fu_header + payload)

            fu_header = fu_header_middle
        assert offset == len(data), "incorrect fragment data"

        return packages

    @staticmethod
    def _packetize_stap_a(
        data: bytes, packages_iterator: Iterator[bytes]
    ) -> tuple[bytes, bytes]:
        counter = 0
        available_size = PACKET_MAX - STAP_A_HEADER_SIZE

        stap_header = NAL_TYPE_STAP_A | (data[0] & 0xE0)

        payload = bytes()
        try:
            nalu = data  # with header
            while len(nalu) <= available_size and counter < 9:
                stap_header |= nalu[0] & 0x80

                nri = nalu[0] & 0x60
                if stap_header & 0x60 < nri:
                    stap_header = stap_header & 0x9F | nri

                available_size -= LENGTH_FIELD_SIZE + len(nalu)
                counter += 1
                payload += pack("!H", len(nalu)) + nalu
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
    def _split_bitstream(buf: bytes) -> Iterator[bytes]:
        # Translated from: https://github.com/aizvorski/h264bitstream/blob/master/h264_nal.c#L134
        i = 0
        while True:
            # Find the start of the NAL unit.
            #
            # NAL Units start with the 3-byte start code 0x000001 or
            # the 4-byte start code 0x00000001.
            i = buf.find(b"\x00\x00\x01", i)
            if i == -1:
                return

            # Jump past the start code
            i += 3
            nal_start = i

            # Find the end of the NAL unit (end of buffer OR next start code)
            i = buf.find(b"\x00\x00\x01", i)
            if i == -1:
                yield buf[nal_start : len(buf)]
                return
            elif buf[i - 1] == 0:
                # 4-byte start code case, jump back one byte
                yield buf[nal_start : i - 1]
            else:
                yield buf[nal_start:i]

    @classmethod
    def _packetize(cls, packages: Iterable[bytes]) -> list[bytes]:
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

    def _encode_frame(
        self, frame: av.VideoFrame, force_keyframe: bool
    ) -> Iterator[bytes]:
        if self.codec and (
            frame.width != self.codec.width
            or frame.height != self.codec.height
            # we only adjust bitrate if it changes by over 10%
            or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate
            > 0.1
        ):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None

        if force_keyframe:
            # force a complete image
            frame.pict_type = av.video.frame.PictureType.I
        else:
            # reset the picture type, otherwise no B-frames are produced
            frame.pict_type = av.video.frame.PictureType.NONE

        if self.codec is None:
            self.codec = av.CodecContext.create("libx264", "w")
            self.codec.width = frame.width
            self.codec.height = frame.height
            self.codec.bit_rate = self.target_bitrate
            self.codec.pix_fmt = "yuv420p"
            self.codec.framerate = fractions.Fraction(MAX_FRAME_RATE, 1)
            self.codec.time_base = fractions.Fraction(1, MAX_FRAME_RATE)
            self.codec.options = {
                "level": "31",
                "tune": "zerolatency",
            }
            self.codec.profile = "Baseline"

        data_to_send = b""
        for package in self.codec.encode(frame):
            data_to_send += bytes(package)

        if data_to_send:
            yield from self._split_bitstream(data_to_send)

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        assert isinstance(frame, av.VideoFrame)
        packages = self._encode_frame(frame, force_keyframe)
        timestamp = convert_timebase(frame.pts, frame.time_base, VIDEO_TIME_BASE)
        return self._packetize(packages), timestamp

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        assert isinstance(packet, av.Packet)
        packages = self._split_bitstream(bytes(packet))
        timestamp = convert_timebase(packet.pts, packet.time_base, VIDEO_TIME_BASE)
        return self._packetize(packages), timestamp

    @property
    def target_bitrate(self) -> int:
        """
        Target bitrate in bits per second.
        """
        return self.__target_bitrate

    @target_bitrate.setter
    def target_bitrate(self, bitrate: int) -> None:
        bitrate = max(MIN_BITRATE, min(bitrate, MAX_BITRATE))
        self.__target_bitrate = bitrate


def h264_depayload(payload: bytes) -> bytes:
    descriptor, data = H264PayloadDescriptor.parse(payload)
    return data
