import logging
import multiprocessing
import random
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


class Vp9PayloadDescriptor:
    """
    VP9 RTP Payload Descriptor (RFC 9628)

    This implementation supports BASIC mode:
    - Non-flexible mode (F=0)
    - Single layer (no spatial/temporal scalability in Phase 1)
    - Picture ID (7 or 15 bits)
    - Layer indices and TL0PICIDX for non-flexible mode
    """

    def __init__(
        self,
        # Required flags (first byte)
        picture_id_present: bool = False,
        inter_picture_predicted: bool = False,
        layer_indices_present: bool = False,
        flexible_mode: bool = False,
        start_of_frame: bool = False,
        end_of_frame: bool = False,
        scalability_structure_present: bool = False,
        not_reference_frame: bool = False,
        # Optional fields
        picture_id: Optional[int] = None,
        tl0picidx: Optional[int] = None,
        # Layer indices (basic support)
        temporal_id: Optional[int] = None,
        switching_up_point: Optional[bool] = None,
        spatial_id: Optional[int] = None,
        inter_layer_dependency: Optional[bool] = None,
    ) -> None:
        # Store all fields using RFC 9628 naming convention
        self.I = picture_id_present
        self.P = inter_picture_predicted
        self.L = layer_indices_present
        self.F = flexible_mode
        self.B = start_of_frame
        self.E = end_of_frame
        self.V = scalability_structure_present
        self.Z = not_reference_frame

        self.picture_id = picture_id
        self.tl0picidx = tl0picidx

        # Layer info
        self.tid = temporal_id
        self.u = switching_up_point
        self.sid = spatial_id
        self.d = inter_layer_dependency

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
        if self.I:
            byte0 |= 0x80  # 0b10000000
        if self.P:
            byte0 |= 0x40  # 0b01000000
        if self.L:
            byte0 |= 0x20  # 0b00100000
        if self.F:
            byte0 |= 0x10  # 0b00010000
        if self.B:
            byte0 |= 0x08  # 0b00001000
        if self.E:
            byte0 |= 0x04  # 0b00000100
        if self.V:
            byte0 |= 0x02  # 0b00000010
        if self.Z:
            byte0 |= 0x01  # 0b00000001

        data.append(byte0)

        # === PICTURE ID (if I=1) ===
        if self.I and self.picture_id is not None:
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
        if self.L:
            layer_byte = 0
            if self.tid is not None:
                layer_byte |= (self.tid & 0x07) << 5  # TID: bits 7-5
            if self.u:
                layer_byte |= 0x10  # U: bit 4
            if self.sid is not None:
                layer_byte |= (self.sid & 0x07) << 1  # SID: bits 3-1
            if self.d:
                layer_byte |= 0x01  # D: bit 0
            data.append(layer_byte)

        # === NON-FLEXIBLE MODE: TL0PICIDX (if F=0 and L=1) ===
        if not self.F and self.L and self.tl0picidx is not None:
            data.append(self.tl0picidx & 0xFF)

        # === FLEXIBLE MODE: Reference indices (Phase 2 - TODO) ===
        # if self.F and self.P:
        #     # P_DIFF implementation
        #     pass

        # === SCALABILITY STRUCTURE (if V=1) (Phase 2 - TODO) ===
        # if self.V:
        #     # SS data implementation
        #     pass

        return bytes(data)

    def __repr__(self) -> str:
        """Debug string representation."""
        flags = []
        if self.I:
            flags.append(f"pic_id={self.picture_id}")
        if self.P:
            flags.append("P")
        if self.B:
            flags.append("B")
        if self.E:
            flags.append("E")
        if self.F:
            flags.append("F")
        if self.L:
            flags.append(f"TID={self.tid},SID={self.sid}")
        if not self.F and self.tl0picidx is not None:
            flags.append(f"TL0={self.tl0picidx}")

        return f"Vp9PayloadDescriptor({', '.join(flags)})"

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

        I = bool(byte0 & 0x80)  # Picture ID present
        P = bool(byte0 & 0x40)  # Inter-picture predicted
        L = bool(byte0 & 0x20)  # Layer indices present
        F = bool(byte0 & 0x10)  # Flexible mode
        B = bool(byte0 & 0x08)  # Start of frame
        E = bool(byte0 & 0x04)  # End of frame
        V = bool(byte0 & 0x02)  # Scalability structure present
        Z = bool(byte0 & 0x01)  # Not reference frame

        logger.debug(f"VP9 parse: byte0=0x{byte0:02x} I={I} P={P} L={L} F={F} B={B} E={E} V={V} Z={Z} len={len(data)}")

        picture_id = None
        tl0picidx = None
        tid = None
        u = None
        sid = None
        d = None

        # === PICTURE ID (if I=1) ===
        if I:
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
        if L:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated Layer Indices")

            layer_byte = data[pos]
            pos += 1

            tid = (layer_byte >> 5) & 0x07  # Bits 7-5
            u = bool(layer_byte & 0x10)  # Bit 4
            sid = (layer_byte >> 1) & 0x07  # Bits 3-1
            d = bool(layer_byte & 0x01)  # Bit 0

        # === NON-FLEXIBLE MODE: TL0PICIDX (if F=0 and L=1) ===
        if not F and L:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated TL0PICIDX")
            tl0picidx = data[pos]
            pos += 1

        # === FLEXIBLE MODE: Reference indices (if F=1 and P=1) ===
        # Reference indices: P_DIFF (up to 3 times)
        # +-+-+-+-+-+-+-+-+
        # | P_DIFF      |N|  N=1 means another P_DIFF follows
        # +-+-+-+-+-+-+-+-+
        if F and P:
            max_ref_pics = 3
            pdiff_count = 0
            while True:
                if len(data) <= pos:
                    raise ValueError("VP9 descriptor has truncated P_DIFF")

                # P_DIFF is in bits 7-1, N flag is bit 0
                # pdiff_value = data[pos] >> 1  # We don't store these for now
                n_flag = data[pos] & 0x01  # N=1 means more P_DIFF follows
                pos += 1
                pdiff_count += 1

                if n_flag == 0:  # No more P_DIFF
                    break

                if pdiff_count >= max_ref_pics:
                    raise ValueError("VP9 descriptor has too many P_DIFF entries")

        # === SCALABILITY STRUCTURE (if V=1) ===
        # Scalability structure format:
        # +-+-+-+-+-+-+-+-+
        # | N_S |Y|G|-|-|-|
        # +-+-+-+-+-+-+-+-+
        # Then optionally WIDTH/HEIGHT for N_S+1 layers (if Y=1)
        # Then optionally N_G and picture group data (if G=1)
        if V:
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
                    # WIDTH (2 bytes) + HEIGHT (2 bytes)
                    # width = (data[pos] << 8) | data[pos + 1]
                    # height = (data[pos + 2] << 8) | data[pos + 3]
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
                    # TID (bits 7-5), U (bit 4), R (bits 3-2)
                    r_count = (pg_byte >> 2) & 0x03  # Number of reference diffs for this picture
                    pos += 1

                    # Skip R P_DIFF bytes
                    if len(data) <= pos + r_count - 1:
                        raise ValueError("VP9 descriptor has truncated PG P_DIFF")
                    pos += r_count

        # Create descriptor object
        descriptor = cls(
            picture_id_present=I,
            inter_picture_predicted=P,
            layer_indices_present=L,
            flexible_mode=F,
            start_of_frame=B,
            end_of_frame=E,
            scalability_structure_present=V,
            not_reference_frame=Z,
            picture_id=picture_id,
            tl0picidx=tl0picidx,
            temporal_id=tid,
            switching_up_point=u,
            spatial_id=sid,
            inter_layer_dependency=d,
        )

        # Return descriptor and remaining payload
        remaining_data = data[pos:]
        logger.debug(f"VP9 parse complete: descriptor_len={pos}, payload_len={len(remaining_data)}, first_bytes={remaining_data[:16].hex() if len(remaining_data) >= 16 else remaining_data.hex()}")
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
    """

    def __init__(self) -> None:
        self.codec: Optional[VideoCodecContext] = None
        self.picture_id = random.randint(0, (1 << 15) - 1)  # 15-bit picture ID
        self.tl0picidx = 0  # Temporal layer zero index
        self.__target_bitrate = DEFAULT_BITRATE

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

        # Force keyframe if requested
        if force_keyframe:
            frame.pict_type = av.video.frame.PictureType.I

        # Initialize codec on first frame or after reset
        if self.codec is None:
            self._init_codec(frame.width, frame.height)

        try:
            # Encode frame using libvpx-vp9
            data_to_send = b""
            for packet in self.codec.encode(frame):
                data_to_send += bytes(packet)
        except Exception as e:
            logger.warning("Vp9Encoder() failed to encode: " + str(e))
            return [], 0

        # Packetize encoded data
        payloads = self._packetize(data_to_send, self.picture_id, self.tl0picidx)

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

    @classmethod
    def _packetize(
        cls, buffer: bytes, picture_id: int, tl0picidx: int
    ) -> list[bytes]:
        """
        Packetize VP9 encoded data into RTP payloads.

        Each payload = VP9 descriptor + VP9 frame fragment

        Args:
            buffer: Encoded VP9 frame data
            picture_id: Current picture ID
            tl0picidx: Temporal layer zero index

        Returns:
            List of RTP payload bytes (each ≤ PACKET_MAX)
        """
        payloads = []
        length = len(buffer)

        if length == 0:
            return payloads

        pos = 0
        is_first = True

        while pos < length:
            # Determine if this is the last packet
            # (need to know before building descriptor)
            remaining = length - pos
            is_last = remaining <= PACKET_MAX or (pos + PACKET_MAX >= length)

            # Create descriptor for this packet
            # Match browser format: flexible mode, no layer indices
            descriptor = Vp9PayloadDescriptor(
                picture_id_present=True,
                picture_id=picture_id,
                inter_picture_predicted=False,  # TODO: detect from frame type
                layer_indices_present=False,  # Match browser: no layer indices
                flexible_mode=True,  # Match browser: flexible mode
                start_of_frame=is_first,
                end_of_frame=is_last,
                scalability_structure_present=False,  # No SS data
                not_reference_frame=True,  # Match browser: Z=1
                temporal_id=None,  # No layer indices
                switching_up_point=None,
                spatial_id=None,
                inter_layer_dependency=None,
                tl0picidx=None,  # Not used in flexible mode
            )

            descriptor_bytes = bytes(descriptor)
            available_size = PACKET_MAX - len(descriptor_bytes)
            payload_size = min(remaining, available_size)

            # Build RTP payload: descriptor + data fragment
            payload = descriptor_bytes + buffer[pos : pos + payload_size]
            payloads.append(payload)

            pos += payload_size
            is_first = False

        return payloads

    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        """
        Pack a pre-encoded VP9 packet for RTP transmission.

        Used when passing through VP9 data without re-encoding.
        """
        payloads = self._packetize(bytes(packet), self.picture_id, self.tl0picidx)
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
    try:
        descriptor, data = Vp9PayloadDescriptor.parse(payload)
        logger.debug(f"VP9 depayload: {descriptor}, data_len={len(data)}, first_bytes={data[:16].hex() if len(data) >= 16 else data.hex()}")
        return data
    except Exception as e:
        logger.error(f"VP9 depayload failed: {e}, payload_len={len(payload)}, first_bytes={payload[:16].hex() if len(payload) >= 16 else payload.hex()}")
        raise
