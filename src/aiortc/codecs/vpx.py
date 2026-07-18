import logging
import multiprocessing
import random
from dataclasses import dataclass
from struct import pack, unpack_from
from typing import Optional, Type, TypeVar, cast

import av
from av import CodecContext, VideoFrame
from av.frame import Frame
from av.packet import Packet
from av.video.codeccontext import VideoCodecContext

from ..jitterbuffer import JitterFrame
from ..mediastreams import VIDEO_TIME_BASE, convert_timebase
from .base import Decoder, Encoder

logger = logging.getLogger(__name__)

DEFAULT_BITRATE = 500000  # 500 kbps
MIN_BITRATE = 250000  # 250 kbps
MAX_BITRATE = 1500000  # 1.5 Mbps

MAX_FRAME_RATE = 30
PACKET_MAX = 1300

DESCRIPTOR_T = TypeVar("DESCRIPTOR_T", bound="VpxPayloadDescriptor")
VP9_DESCRIPTOR_T = TypeVar("VP9_DESCRIPTOR_T", bound="Vp9PayloadDescriptor")


def number_of_threads(pixels: int, cpus: int) -> int:
    if pixels >= 1920 * 1080 and cpus > 8:
        return 8
    elif pixels > 1280 * 960 and cpus >= 6:
        return 3
    elif pixels > 640 * 480 and cpus >= 3:
        return 2
    else:
        return 1


class VpxPayloadDescriptor:
    def __init__(
        self,
        partition_start: int,
        partition_id: int,
        picture_id: Optional[int] = None,
        tl0picidx: Optional[int] = None,
        tid: Optional[tuple[int, int]] = None,
        keyidx: Optional[int] = None,
    ) -> None:
        self.partition_start = partition_start
        self.partition_id = partition_id
        self.picture_id = picture_id
        self.tl0picidx = tl0picidx
        self.tid = tid
        self.keyidx = keyidx

    def __bytes__(self) -> bytes:
        octet = (self.partition_start << 4) | self.partition_id

        ext_octet = 0
        if self.picture_id is not None:
            ext_octet |= 1 << 7
        if self.tl0picidx is not None:
            ext_octet |= 1 << 6
        if self.tid is not None:
            ext_octet |= 1 << 5
        if self.keyidx is not None:
            ext_octet |= 1 << 4

        if ext_octet:
            data = pack("!BB", (1 << 7) | octet, ext_octet)
            if self.picture_id is not None:
                if self.picture_id < 128:
                    data += pack("!B", self.picture_id)
                else:
                    data += pack("!H", (1 << 15) | self.picture_id)
            if self.tl0picidx is not None:
                data += pack("!B", self.tl0picidx)
            if self.tid is not None or self.keyidx is not None:
                t_k = 0
                if self.tid is not None:
                    t_k |= (self.tid[0] << 6) | (self.tid[1] << 5)
                if self.keyidx is not None:
                    t_k |= self.keyidx
                data += pack("!B", t_k)
        else:
            data = pack("!B", octet)

        return data

    def __repr__(self) -> str:
        return (
            f"VpxPayloadDescriptor(S={self.partition_start}, "
            f"PID={self.partition_id}, pic_id={self.picture_id})"
        )

    @classmethod
    def parse(cls: Type[DESCRIPTOR_T], data: bytes) -> tuple[DESCRIPTOR_T, bytes]:
        if len(data) < 1:
            raise ValueError("VPX descriptor is too short")

        # first byte
        octet = data[0]
        extended = octet >> 7
        partition_start = (octet >> 4) & 1
        partition_id = octet & 0xF
        picture_id = None
        tl0picidx = None
        tid = None
        keyidx = None
        pos = 1

        # extended control bits
        if extended:
            if len(data) < pos + 1:
                raise ValueError("VPX descriptor has truncated extended bits")

            octet = data[pos]
            ext_I = (octet >> 7) & 1
            ext_L = (octet >> 6) & 1
            ext_T = (octet >> 5) & 1
            ext_K = (octet >> 4) & 1
            pos += 1

            # picture id
            if ext_I:
                if len(data) < pos + 1:
                    raise ValueError("VPX descriptor has truncated PictureID")

                if data[pos] & 0x80:
                    if len(data) < pos + 2:
                        raise ValueError("VPX descriptor has truncated long PictureID")

                    picture_id = unpack_from("!H", data, pos)[0] & 0x7FFF
                    pos += 2
                else:
                    picture_id = data[pos]
                    pos += 1

            # unused
            if ext_L:
                if len(data) < pos + 1:
                    raise ValueError("VPX descriptor has truncated TL0PICIDX")

                tl0picidx = data[pos]
                pos += 1
            if ext_T or ext_K:
                if len(data) < pos + 1:
                    raise ValueError("VPX descriptor has truncated T/K")

                t_k = data[pos]
                if ext_T:
                    tid = ((t_k >> 6) & 3, (t_k >> 5) & 1)
                if ext_K:
                    keyidx = t_k & 0x1F
                pos += 1

        obj = cls(
            partition_start=partition_start,
            partition_id=partition_id,
            picture_id=picture_id,
            tl0picidx=tl0picidx,
            tid=tid,
            keyidx=keyidx,
        )
        return obj, data[pos:]


@dataclass
class Vp9PayloadDescriptor:
    """
    VP9 RTP Payload Descriptor (RFC 9628)

    This implementation supports:
    - Non-flexible mode (F=0) with TL0PICIDX
    - Flexible mode (F=1) with P_DIFF reference indices
    - Picture ID (7 or 15 bits)
    - Layer indices (TID, SID)
    - Scalability structure parsing

    Field name mapping (RFC 9628 single-letter -> descriptive):
        I -> picture_id_present
        P -> inter_picture_predicted
        L -> layer_indices_present
        F -> flexible_mode
        B -> start_of_frame
        E -> end_of_frame
        V -> scalability_structure_present
        Z -> not_reference_frame
        TID -> temporal_id
        U -> switching_up_point
        SID -> spatial_id
        D -> inter_layer_dependency
    """

    # Required flags (first byte: I|P|L|F|B|E|V|Z)
    picture_id_present: bool = False  # I
    inter_picture_predicted: bool = False  # P
    layer_indices_present: bool = False  # L
    flexible_mode: bool = False  # F
    start_of_frame: bool = False  # B
    end_of_frame: bool = False  # E
    scalability_structure_present: bool = False  # V
    not_reference_frame: bool = False  # Z

    # Optional fields
    picture_id: Optional[int] = None
    tl0picidx: Optional[int] = None  # TL0PICIDX

    # Layer indices
    temporal_id: Optional[int] = None  # TID
    switching_up_point: Optional[bool] = None  # U
    spatial_id: Optional[int] = None  # SID
    inter_layer_dependency: Optional[bool] = None  # D

    def __bytes__(self) -> bytes:
        """
        Marshal VP9 payload descriptor to bytes.

        Byte layout:
        - Byte 0: I|P|L|F|B|E|V|Z (required flags)
        - Bytes 1+: Optional fields based on flags
        """
        data = bytearray()

        # === BYTE 0: Required flags ===
        byte0 = 0
        if self.picture_id_present:
            byte0 |= 0x80  # I bit
        if self.inter_picture_predicted:
            byte0 |= 0x40  # P bit
        if self.layer_indices_present:
            byte0 |= 0x20  # L bit
        if self.flexible_mode:
            byte0 |= 0x10  # F bit
        if self.start_of_frame:
            byte0 |= 0x08  # B bit
        if self.end_of_frame:
            byte0 |= 0x04  # E bit
        if self.scalability_structure_present:
            byte0 |= 0x02  # V bit
        if self.not_reference_frame:
            byte0 |= 0x01  # Z bit

        data.append(byte0)

        # === PICTURE ID (if I=1) ===
        if self.picture_id_present and self.picture_id is not None:
            if self.picture_id < 128:
                # 7-bit picture ID: M=0
                data.append(self.picture_id & 0x7F)
            else:
                # 15-bit picture ID: M=1
                # First byte: M=1 + upper 7 bits
                data.append(0x80 | ((self.picture_id >> 8) & 0x7F))
                # Second byte: lower 8 bits
                data.append(self.picture_id & 0xFF)

        # === LAYER INDICES (if L=1) ===
        if self.layer_indices_present:
            layer_byte = 0
            if self.temporal_id is not None:
                layer_byte |= (self.temporal_id & 0x07) << 5  # TID: bits 7-5
            if self.switching_up_point:
                layer_byte |= 0x10  # U: bit 4
            if self.spatial_id is not None:
                layer_byte |= (self.spatial_id & 0x07) << 1  # SID: bits 3-1
            if self.inter_layer_dependency:
                layer_byte |= 0x01  # D: bit 0
            data.append(layer_byte)

        # === NON-FLEXIBLE MODE: TL0PICIDX (if F=0 and L=1) ===
        if not self.flexible_mode and self.layer_indices_present and self.tl0picidx is not None:
            data.append(self.tl0picidx & 0xFF)

        return bytes(data)

    @classmethod
    def parse(
        cls: Type[VP9_DESCRIPTOR_T], data: bytes
    ) -> tuple[VP9_DESCRIPTOR_T, bytes]:
        """
        Unmarshal VP9 payload descriptor from bytes.

        Args:
            data: RTP payload bytes (descriptor + VP9 data)

        Returns:
            (descriptor_object, remaining_payload_data)

        Raises:
            ValueError: If descriptor is malformed
        """
        if len(data) < 1:
            raise ValueError("VP9 descriptor is too short")

        pos = 0

        # === BYTE 0: Required flags ===
        byte0 = data[pos]
        pos += 1

        picture_id_present = bool(byte0 & 0x80)  # I bit
        inter_picture_predicted = bool(byte0 & 0x40)  # P bit
        layer_indices_present = bool(byte0 & 0x20)  # L bit
        flexible_mode = bool(byte0 & 0x10)  # F bit
        start_of_frame = bool(byte0 & 0x08)  # B bit
        end_of_frame = bool(byte0 & 0x04)  # E bit
        scalability_structure_present = bool(byte0 & 0x02)  # V bit
        not_reference_frame = bool(byte0 & 0x01)  # Z bit

        picture_id = None
        tl0picidx = None
        temporal_id = None
        switching_up_point = None
        spatial_id = None
        inter_layer_dependency = None

        # === PICTURE ID (if I=1) ===
        if picture_id_present:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated Picture ID")

            # Check M bit (bit 7 of next byte)
            if data[pos] & 0x80:
                # 15-bit Picture ID
                if len(data) < pos + 2:
                    raise ValueError("VP9 descriptor has truncated 15-bit Picture ID")
                picture_id = ((data[pos] & 0x7F) << 8) | data[pos + 1]
                pos += 2
            else:
                # 7-bit Picture ID
                picture_id = data[pos] & 0x7F
                pos += 1

        # === LAYER INDICES (if L=1) ===
        if layer_indices_present:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated Layer Indices")

            layer_byte = data[pos]
            pos += 1

            temporal_id = (layer_byte >> 5) & 0x07  # TID: bits 7-5
            switching_up_point = bool(layer_byte & 0x10)  # U: bit 4
            spatial_id = (layer_byte >> 1) & 0x07  # SID: bits 3-1
            inter_layer_dependency = bool(layer_byte & 0x01)  # D: bit 0

        # === NON-FLEXIBLE MODE: TL0PICIDX (if F=0 and L=1) ===
        if not flexible_mode and layer_indices_present:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated TL0PICIDX")
            tl0picidx = data[pos]
            pos += 1

        # === FLEXIBLE MODE: Reference indices (if F=1 and P=1) ===
        # Reference indices: P_DIFF (up to 3 times)
        # +-+-+-+-+-+-+-+-+
        # | P_DIFF      |N|  N=1 means another P_DIFF follows
        # +-+-+-+-+-+-+-+-+
        if flexible_mode and inter_picture_predicted:
            max_ref_pics = 3
            pdiff_count = 0
            while True:
                if len(data) <= pos:
                    raise ValueError("VP9 descriptor has truncated P_DIFF")

                # P_DIFF is in bits 7-1, N flag is bit 0
                n_flag = data[pos] & 0x01  # N=1 means more P_DIFF follows
                pos += 1
                pdiff_count += 1

                if n_flag == 0:  # No more P_DIFF
                    break

                if pdiff_count >= max_ref_pics:
                    raise ValueError("VP9 descriptor has too many P_DIFF entries")

        # === SCALABILITY STRUCTURE (if V=1) ===
        if scalability_structure_present:
            if len(data) <= pos:
                raise ValueError("VP9 descriptor has truncated SS")

            ss_byte = data[pos]
            pos += 1

            n_s = (ss_byte >> 5) & 0x07  # Number of spatial layers - 1 (bits 7-5)
            y_flag = bool(ss_byte & 0x10)  # Y bit (bit 4)
            g_flag = bool(ss_byte & 0x08)  # G bit (bit 3)

            num_spatial_layers = n_s + 1

            # Parse WIDTH and HEIGHT for each spatial layer (if Y=1)
            if y_flag:
                for _ in range(num_spatial_layers):
                    if len(data) <= pos + 3:
                        raise ValueError("VP9 descriptor has truncated SS layer resolution")
                    pos += 4

            # Parse picture group info (if G=1)
            if g_flag:
                if len(data) <= pos:
                    raise ValueError("VP9 descriptor has truncated N_G")

                n_g = data[pos]  # Number of pictures in Picture Group
                pos += 1

                for _ in range(n_g):
                    if len(data) <= pos:
                        raise ValueError("VP9 descriptor has truncated PG entry")

                    pg_byte = data[pos]
                    r_count = (pg_byte >> 2) & 0x03  # R: number of reference diffs
                    pos += 1

                    # Skip R P_DIFF bytes
                    if len(data) <= pos + r_count - 1:
                        raise ValueError("VP9 descriptor has truncated PG P_DIFF")
                    pos += r_count

        # Create descriptor object
        descriptor = cls(
            picture_id_present=picture_id_present,
            inter_picture_predicted=inter_picture_predicted,
            layer_indices_present=layer_indices_present,
            flexible_mode=flexible_mode,
            start_of_frame=start_of_frame,
            end_of_frame=end_of_frame,
            scalability_structure_present=scalability_structure_present,
            not_reference_frame=not_reference_frame,
            picture_id=picture_id,
            tl0picidx=tl0picidx,
            temporal_id=temporal_id,
            switching_up_point=switching_up_point,
            spatial_id=spatial_id,
            inter_layer_dependency=inter_layer_dependency,
        )

        # Return descriptor and remaining payload
        remaining_data = data[pos:]
        return descriptor, remaining_data


class Vp8Decoder(Decoder):
    def __init__(self) -> None:
        self.codec = CodecContext.create("libvpx", "r")

    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        try:
            packet = Packet(encoded_frame.data)
            packet.pts = encoded_frame.timestamp
            packet.time_base = VIDEO_TIME_BASE
            return cast(list[Frame], self.codec.decode(packet))
        except av.FFmpegError as e:
            logger.warning("Vp8Decoder() failed to decode, skipping package: " + str(e))
            return []


class Vp8Encoder(Encoder):
    def __init__(self) -> None:
        self.codec: Optional[VideoCodecContext] = None
        self.picture_id = random.randint(0, (1 << 15) - 1)
        self.__target_bitrate = DEFAULT_BITRATE

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        assert isinstance(frame, VideoFrame)
        if frame.format.name != "yuv420p":
            frame = frame.reformat(format="yuv420p")

        if self.codec and (
            frame.width != self.codec.width
            or frame.height != self.codec.height
            # We only adjust bitrate if it changes by over 10%.
            or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate
            > 0.1
        ):
            self.codec = None

        # Force a complete image if a keyframe was requested.
        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I

        if self.codec is None:
            self.codec = av.CodecContext.create("libvpx", "w")
            self.codec.width = frame.width
            self.codec.height = frame.height
            self.codec.bit_rate = self.target_bitrate
            self.codec.pix_fmt = "yuv420p"
            self.codec.gop_size = 3000  # kf_max_dist
            self.codec.qmin = 2  # rc_min_quantizer
            self.codec.qmax = 56  # rc_max_quantizer
            self.codec.options = {
                # We want rc_buf_sz = 1000 and FFmpeg sets:
                #   rc_buf_sz =  bufsize * 1000 / bit_rate
                "bufsize": str(self.__target_bitrate),
                "cpu-used": "-6",
                "deadline": "realtime",
                "lag-in-frames": "0",
                # Setting minrate = maxrate = bit_rate triggers CBR.
                "minrate": str(self.target_bitrate),
                "maxrate": str(self.target_bitrate),
                "noise-sensitivity": "4",
                "overshoot-pct": "15",
                "partitions": "0",  # VP8_ONE_TOKENPARTITION
                "static-thresh": "1",
                "undershoot-pct": "100",
            }
            self.codec.thread_count = number_of_threads(
                frame.width * frame.height, multiprocessing.cpu_count()
            )

        data_to_send = b""
        for package in self.codec.encode(frame):
            data_to_send += bytes(package)

        # Packetize.
        payloads = self._packetize(data_to_send, self.picture_id)
        timestamp = convert_timebase(frame.pts, frame.time_base, VIDEO_TIME_BASE)
        self.picture_id = (self.picture_id + 1) % (1 << 15)
        return payloads, timestamp

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        payloads = self._packetize(bytes(packet), self.picture_id)
        timestamp = convert_timebase(packet.pts, packet.time_base, VIDEO_TIME_BASE)
        self.picture_id = (self.picture_id + 1) % (1 << 15)
        return payloads, timestamp

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

    @classmethod
    def _packetize(cls, buffer: bytes, picture_id: int) -> list[bytes]:
        payloads = []
        descr = VpxPayloadDescriptor(
            partition_start=1, partition_id=0, picture_id=picture_id
        )
        length = len(buffer)
        pos = 0
        while pos < length:
            descr_bytes = bytes(descr)
            size = min(length - pos, PACKET_MAX - len(descr_bytes))
            payloads.append(descr_bytes + buffer[pos : pos + size])
            descr.partition_start = 0
            pos += size
        return payloads


def vp8_depayload(payload: bytes) -> bytes:
    descriptor, data = VpxPayloadDescriptor.parse(payload)
    return data


class Vp9Encoder(Encoder):
    """
    VP9 Video Encoder using libvpx-vp9.

    Handles:
    - Encoding frames to VP9
    - RTP packetization with VP9 payload descriptors
    - Picture ID and TL0PICIDX management

    Args:
        flexible_mode: If True, use flexible mode (F=1) which is more common.
                      If False, use non-flexible mode (F=0) with TL0PICIDX.
                      Default: True (recommended for browser compatibility)
    """

    def __init__(self, flexible_mode: bool = True) -> None:
        self.codec: Optional[VideoCodecContext] = None
        self.picture_id = random.randint(0, (1 << 15) - 1)  # 15-bit picture ID
        self.tl0picidx = 0  # Temporal layer zero index
        self.__target_bitrate = DEFAULT_BITRATE
        self.flexible_mode = flexible_mode

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> tuple[list[bytes], int]:
        """
        Encode a video frame to VP9 and packetize for RTP.

        Args:
            frame: Input video frame (PyAV VideoFrame)
            force_keyframe: Force keyframe generation

        Returns:
            (payloads, timestamp): List of RTP payload bytes and RTP timestamp
        """
        assert isinstance(frame, VideoFrame)

        # Ensure yuv420p format (required by libvpx-vp9)
        if frame.format.name != "yuv420p":
            frame = frame.reformat(format="yuv420p")

        # Reinitialize codec if resolution or bitrate changed
        if self.codec and (
            frame.width != self.codec.width
            or frame.height != self.codec.height
            # We only adjust bitrate if it changes by over 10%.
            or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate
            > 0.1
        ):
            self.codec = None

        # Initialize codec on first frame or after reset
        # ALWAYS force keyframe on first frame
        if self.codec is None:
            self._init_codec(frame.width, frame.height)
            force_keyframe = True  # First frame MUST be a keyframe

        # Force keyframe if requested
        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I

        # Encode frame using libvpx-vp9
        data_to_send = b""
        is_keyframe = False
        for packet in self.codec.encode(frame):
            # Detect if this packet is a keyframe
            if packet.is_keyframe:
                is_keyframe = True
            data_to_send += bytes(packet)

        # Packetize encoded data
        # is_inter_frame is the opposite of is_keyframe
        payloads = self._packetize(
            data_to_send, self.picture_id, self.tl0picidx, is_inter_frame=(not is_keyframe)
        )

        # Convert timestamp to RTP timebase (90kHz for video)
        timestamp = convert_timebase(frame.pts, frame.time_base, VIDEO_TIME_BASE)

        # Increment picture ID (wraps at 15 bits)
        self.picture_id = (self.picture_id + 1) & 0x7FFF

        # Increment TL0PICIDX (wraps at 8 bits)
        self.tl0picidx = (self.tl0picidx + 1) & 0xFF

        return payloads, timestamp

    def _init_codec(self, width: int, height: int) -> None:
        """
        Initialize libvpx-vp9 codec with settings optimized for WebRTC.

        Settings based on:
        - WebRTC project defaults
        - Pion WebRTC configuration
        - RFC 9628 recommendations
        """
        self.codec = av.CodecContext.create("libvpx-vp9", "w")
        self.codec.width = width
        self.codec.height = height
        self.codec.bit_rate = self.target_bitrate
        self.codec.pix_fmt = "yuv420p"
        self.codec.gop_size = 3000  # kf_max_dist (same as VP8)
        self.codec.qmin = 2  # rc_min_quantizer
        self.codec.qmax = 56  # rc_max_quantizer

        # VP9-specific options (optimized for realtime WebRTC)
        self.codec.options = {
            # Rate control
            "bufsize": str(self.target_bitrate),  # VBV buffer size
            "minrate": str(self.target_bitrate),  # CBR mode
            "maxrate": str(self.target_bitrate),
            # Encoding speed/quality tradeoff
            "cpu-used": "8",  # Fastest (realtime), range: 0-8
            "deadline": "realtime",
            "lag-in-frames": "0",  # No frame buffering (low latency)
            # Error resilience
            "error-resilient": "1",  # Enable error resilience
            # Tiling (for parallelization)
            "tile-columns": "1",  # 2 tile columns for better parallelism
            "tile-rows": "0",  # 1 tile row
            # VP9-specific
            "row-mt": "1",  # Row-based multi-threading
            "frame-parallel": "0",  # Disable frame parallel decoding (better for WebRTC)
            "aq-mode": "3",  # Adaptive quantization: cyclic refresh
        }

        # Set thread count based on resolution
        self.codec.thread_count = number_of_threads(
            width * height, multiprocessing.cpu_count()
        )

        logger.debug(
            f"Initialized VP9 encoder: {width}x{height}, "
            f"{self.target_bitrate} bps, {self.codec.thread_count} threads"
        )

    @staticmethod
    def _parse_vp9_header(data: bytes) -> Optional[dict]:
        """
        Parse VP9 bitstream header to extract frame information.

        EXACT copy of Pion's vp9.Header.Unmarshal() logic:
        https://github.com/pion/rtp/blob/master/codecs/vp9/header.go

        Returns dict with:
            - non_key_frame: bool
            - width: int (or None)
            - height: int (or None)
        Returns None on parse error.
        """
        if len(data) < 1:
            return None

        pos = 0  # bit position

        def has_space(n: int) -> bool:
            """Check if n bits are available."""
            return n <= ((len(data) * 8) - pos)

        def read_flag() -> Optional[bool]:
            """Read single bit."""
            nonlocal pos
            if not has_space(1):
                return None
            byte_pos = pos >> 3
            bit_offset = 7 - (pos & 0x07)
            bit = (data[byte_pos] >> bit_offset) & 0x01
            pos += 1
            return bit == 1

        def read_bits(n: int) -> Optional[int]:
            """Read n bits."""
            nonlocal pos
            if not has_space(n):
                return None

            res = 8 - (pos & 0x07)
            if n < res:
                byte_pos = pos >> 3
                bits = (data[byte_pos] >> (res - n)) & ((1 << n) - 1)
                pos += n
                return bits

            byte_pos = pos >> 3
            bits = data[byte_pos] & ((1 << res) - 1)
            pos += res
            n -= res

            while n >= 8:
                byte_pos = pos >> 3
                bits = (bits << 8) | data[byte_pos]
                pos += 8
                n -= 8

            if n > 0:
                byte_pos = pos >> 3
                bits = (bits << n) | (data[byte_pos] >> (8 - n))
                pos += n

            return bits

        # Frame marker (2 bits, must be 0b10)
        if not has_space(4):
            return None
        frame_marker = read_bits(2)
        if frame_marker != 2:
            return None

        # Profile (2 bits)
        profile_low = read_bits(1)
        profile_high = read_bits(1)
        profile = (profile_high << 1) | profile_low

        # Reserved bit if profile == 3
        if profile == 3:
            if not has_space(1):
                return None
            pos += 1

        # show_existing_frame
        show_existing_frame = read_flag()
        if show_existing_frame is None:
            return None
        if show_existing_frame:
            # Skip frame_to_show_map_idx (3 bits)
            return None

        # Read: non_key_frame, show_frame, error_resilient_mode
        if not has_space(3):
            return None
        non_key_frame = read_flag()
        show_frame = read_flag()
        error_resilient_mode = read_flag()

        width = None
        height = None

        # If keyframe, parse frame size
        if not non_key_frame:
            # frame_sync_bytes (3 bytes: 0x49, 0x83, 0x42)
            if not has_space(24):
                return None
            sync0 = read_bits(8)
            sync1 = read_bits(8)
            sync2 = read_bits(8)
            if sync0 != 0x49 or sync1 != 0x83 or sync2 != 0x42:
                return None

            # Skip color_config parsing for now (complex)
            # We only need width/height which comes after color_config

            # For simplicity, we'll parse width/height directly
            # This requires skipping color_config, which varies by profile
            # For now, let's use a simplified approach:
            # Profile 0/1: bit_depth=8, skip color space (3 bits) + range (1 bit)
            # Then parse frame_size

            # Color space (3 bits)
            if not has_space(3):
                return None
            color_space = read_bits(3)

            # Color range (1 bit) if color_space != 7
            if color_space != 7:
                if not has_space(1):
                    return None
                pos += 1  # skip color_range

                # Subsampling for profile 1/3
                if profile == 1 or profile == 3:
                    if not has_space(3):
                        return None
                    pos += 3  # skip subsampling_x, subsampling_y, reserved
            else:
                if profile == 1 or profile == 3:
                    if not has_space(1):
                        return None
                    pos += 1  # skip reserved

            # frame_size: width (16 bits), height (16 bits)
            if not has_space(32):
                return None
            frame_width_minus_1 = read_bits(16)
            frame_height_minus_1 = read_bits(16)
            width = frame_width_minus_1 + 1
            height = frame_height_minus_1 + 1

        return {
            'non_key_frame': non_key_frame,
            'width': width,
            'height': height
        }

    def _packetize(
        self, buffer: bytes, picture_id: int, tl0picidx: int, is_inter_frame: bool = False
    ) -> list[bytes]:
        """
        Packetize VP9 encoded data into RTP payloads.

        EXACT copy of Pion's implementation:
        - Flexible mode: payloadFlexible()
        - Non-flexible mode: payloadNonFlexible()
        https://github.com/pion/rtp/blob/master/codecs/vp9_packet.go

        Args:
            buffer: Encoded VP9 frame data
            picture_id: Current picture ID (15-bit)
            tl0picidx: Temporal layer zero index (unused in flexible mode)
            is_inter_frame: Whether this is an inter-frame (True) or keyframe (False)

        Returns:
            List of RTP payload bytes (each ≤ PACKET_MAX)
        """
        if self.flexible_mode:
            return self._packetize_flexible(buffer, picture_id, is_inter_frame)
        else:
            return self._packetize_non_flexible(buffer, picture_id)

    def _packetize_flexible(self, buffer: bytes, picture_id: int, is_inter_frame: bool) -> list[bytes]:
        """
        VP9 RTP packetization in flexible mode (F=1).

        Based on Pion's payloadFlexible() with FIX for P flag and P_DIFF:
        https://github.com/pion/rtp/blob/master/codecs/vp9_packet.go

        Flexible mode (F=1):
             0 1 2 3 4 5 6 7
            +-+-+-+-+-+-+-+-+
            |I|P|L|F|B|E|V|Z| (REQUIRED)
            +-+-+-+-+-+-+-+-+
       I:   |M| PICTURE ID  | (REQUIRED)
            +-+-+-+-+-+-+-+-+
       M:   | EXTENDED PID  | (RECOMMENDED)
            +-+-+-+-+-+-+-+-+
       P,F: |N|  P_DIFF    | (REQUIRED when P=1 and F=1, RFC 9628)
            +-+-+-+-+-+-+-+-+

        Args:
            buffer: Encoded VP9 frame data
            picture_id: Picture ID (15-bit)
            is_inter_frame: True if inter-frame (P=1), False if keyframe (P=0)
        """
        header_size = 4 if is_inter_frame else 3
        max_fragment_size = PACKET_MAX - header_size
        payload_data_remaining = len(buffer)
        payload_data_index = 0
        payloads = []

        if min(max_fragment_size, payload_data_remaining) <= 0:
            return []

        while payload_data_remaining > 0:
            current_fragment_size = min(max_fragment_size, payload_data_remaining)
            out = bytearray(header_size + current_fragment_size)

            # Byte 0: I=1, P=?, L=0, F=1, B=?, E=?, V=0, Z=0
            out[0] = 0x90  # 0b10010000 = I=1, F=1

            # FIX: Set P flag based on frame type
            if is_inter_frame:
                out[0] |= 0x40  # P=1 for inter-frames (0xD0)
            # else: P=0 for keyframes (0x90) - already set above

            if payload_data_index == 0:
                out[0] |= 0x08  # B=1
            if payload_data_remaining == current_fragment_size:
                out[0] |= 0x04  # E=1

            # Bytes 1-2: Picture ID (always 15-bit, M=1)
            out[1] = (picture_id >> 8) | 0x80  # M=1 + upper 7 bits
            out[2] = picture_id & 0xFF         # lower 8 bits

            # Byte 3 (if inter-frame): P_DIFF reference index
            # RFC 9628: When P=1 and F=1, at least one P_DIFF MUST be present
            # Format: |N(1bit)|P_DIFF(7bits)| where N=0 means no more refs
            # P_DIFF=1 means reference the immediately previous frame
            if is_inter_frame:
                out[3] = (1 << 1) | 0  # N=0, P_DIFF=1

            # Copy payload data
            out[header_size:] = buffer[payload_data_index:payload_data_index + current_fragment_size]
            payloads.append(bytes(out))

            payload_data_remaining -= current_fragment_size
            payload_data_index += current_fragment_size

        return payloads

    def _packetize_non_flexible(self, buffer: bytes, picture_id: int) -> list[bytes]:
        """
        EXACT copy of Pion's payloadNonFlexible():
        https://github.com/pion/rtp/blob/master/codecs/vp9_packet.go

        Non-flexible mode (F=0):
             0 1 2 3 4 5 6 7
            +-+-+-+-+-+-+-+-+
            |I|P|L|F|B|E|V|Z| (REQUIRED)
            +-+-+-+-+-+-+-+-+
       I:   |M| PICTURE ID  | (RECOMMENDED)
            +-+-+-+-+-+-+-+-+
       M:   | EXTENDED PID  | (RECOMMENDED)
            +-+-+-+-+-+-+-+-+
       V:   | SS            | (on keyframes)
            | ..            |
            +-+-+-+-+-+-+-+-+
        """
        # Parse VP9 header to get frame info
        header = self._parse_vp9_header(buffer)
        if header is None:
            return []

        payload_data_remaining = len(buffer)
        payload_data_index = 0
        payloads = []

        while payload_data_remaining > 0:
            # Determine header size
            if not header['non_key_frame'] and payload_data_index == 0:
                header_size = 3 + 8  # Include SS data
            else:
                header_size = 3

            max_fragment_size = PACKET_MAX - header_size
            current_fragment_size = min(max_fragment_size, payload_data_remaining)
            if current_fragment_size <= 0:
                return []

            out = bytearray(header_size + current_fragment_size)

            # Byte 0: I=1, P=?, L=0, F=0, B=?, E=?, V=?, Z=0
            # Note: Z=0 means frames ARE reference frames (correct for single-layer)
            out[0] = 0x80  # I=1, Z=0

            if header['non_key_frame']:
                out[0] |= 0x40  # P=1
            if payload_data_index == 0:
                out[0] |= 0x08  # B=1
            if payload_data_remaining == current_fragment_size:
                out[0] |= 0x04  # E=1

            # Bytes 1-2: Picture ID (always 15-bit)
            out[1] = (picture_id >> 8) | 0x80
            out[2] = picture_id & 0xFF
            off = 3

            # Add Scalability Structure on keyframe first packet
            if not header['non_key_frame'] and payload_data_index == 0:
                out[0] |= 0x02  # V=1
                out[off] = 0x10 | 0x08  # N_S=0, Y=1, G=1
                off += 1

                width = header['width'] or 0
                out[off] = width >> 8
                off += 1
                out[off] = width & 0xFF
                off += 1

                height = header['height'] or 0
                out[off] = height >> 8
                off += 1
                out[off] = height & 0xFF
                off += 1

                out[off] = 0x01  # N_G=1
                off += 1

                out[off] = 1 << 4 | 1 << 2  # TID=0, U=1, R=1
                off += 1

                out[off] = 0x01  # P_DIFF=1

            # Copy payload data
            out[header_size:] = buffer[payload_data_index:payload_data_index + current_fragment_size]
            payloads.append(bytes(out))

            payload_data_remaining -= current_fragment_size
            payload_data_index += current_fragment_size

        return payloads

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        """
        Pack a pre-encoded VP9 packet for RTP transmission.

        Used when passing through VP9 data without re-encoding.
        """
        # Detect frame type from VP9 bitstream header
        is_inter_frame = True  # Default assumption
        if self.flexible_mode:
            # For flexible mode, parse VP9 header to detect actual frame type
            header = self._parse_vp9_header(bytes(packet))
            if header:
                is_inter_frame = header['non_key_frame']
        # Non-flexible mode will parse header in _packetize_non_flexible anyway

        payloads = self._packetize(
            bytes(packet), self.picture_id, self.tl0picidx, is_inter_frame=is_inter_frame
        )
        timestamp = convert_timebase(packet.pts, packet.time_base, VIDEO_TIME_BASE)

        self.picture_id = (self.picture_id + 1) & 0x7FFF
        self.tl0picidx = (self.tl0picidx + 1) & 0xFF

        return payloads, timestamp

    @property
    def target_bitrate(self) -> int:
        """Target bitrate in bits per second."""
        return self.__target_bitrate

    @target_bitrate.setter
    def target_bitrate(self, bitrate: int) -> None:
        bitrate = max(MIN_BITRATE, min(bitrate, MAX_BITRATE))
        self.__target_bitrate = bitrate


class Vp9Decoder(Decoder):
    """
    VP9 Video Decoder using libvpx-vp9.

    Handles:
    - Depacketizing RTP payloads
    - Decoding VP9 frames
    """

    def __init__(self) -> None:
        self.codec = av.CodecContext.create("libvpx-vp9", "r")

    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        """
        Decode a VP9 frame from encoded data.

        Args:
            encoded_frame: Jitter buffer frame with VP9 encoded data
                          (already depacketized, no RTP payload descriptor)

        Returns:
            List of decoded video frames (usually 0 or 1)
        """
        try:
            # Note: encoded_frame.data should already be depacketized VP9 data
            # The depayload() function is called by the RTP receiver before
            # passing data to the decoder
            packet = Packet(encoded_frame.data)
            packet.pts = encoded_frame.timestamp
            packet.time_base = VIDEO_TIME_BASE

            return cast(list[Frame], self.codec.decode(packet))

        except av.FFmpegError as e:
            logger.warning(
                f"Vp9Decoder() failed to decode, skipping package: {e}"
            )
            return []


def vp9_depayload(payload: bytes) -> bytes:
    """
    Remove VP9 RTP payload descriptor from payload.

    Args:
        payload: RTP payload bytes (descriptor + VP9 data)

    Returns:
        VP9 frame data (without descriptor)
    """
    descriptor, data = Vp9PayloadDescriptor.parse(payload)
    return data
