import multiprocessing
import random
from struct import pack, unpack_from
from typing import List, Tuple, Type, TypeVar, cast, Optional

from av import VideoFrame
from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame
from ..mediastreams import VIDEO_CLOCK_RATE, VIDEO_TIME_BASE, convert_timebase
from ._vpx import ffi, lib
from .base import Decoder, Encoder

DEFAULT_BITRATE = 500000  # 500 kbps
MIN_BITRATE = 250000  # 250 kbps
MAX_BITRATE = 1500000  # 1.5 Mbps

MAX_FRAME_RATE = 30
PACKET_MAX = 1300

DESCRIPTOR_T = TypeVar("DESCRIPTOR_T", bound="VP8PayloadDescriptor")


def number_of_threads(pixels: int, cpus: int) -> int:
    if pixels >= 1920 * 1080 and cpus > 8:
        return 8
    elif pixels > 1280 * 960 and cpus >= 6:
        return 3
    elif pixels > 640 * 480 and cpus >= 3:
        return 2
    else:
        return 1


class VP8PayloadDescriptor:
    # https://datatracker.ietf.org/doc/html/rfc7741#section-4.2
    def __init__(
        self,
        partition_start,
        partition_id,
        picture_id=None,
        tl0picidx=None,
        tid=None,
        keyidx=None,
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
            f"VP8PayloadDescriptor(S={self.partition_start}, "
            f"PID={self.partition_id}, pic_id={self.picture_id})"
        )

    @classmethod
    def parse(cls: Type[DESCRIPTOR_T], data: bytes) -> Tuple[DESCRIPTOR_T, bytes]:
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
    # https://datatracker.ietf.org/doc/html/draft-ietf-payload-vp9-16#section-4.2
    def __init__(
        self,
        start_of_layer_frame: int,
        end_of_layer_frame: int,
        picture_id: Optional[int] = None,
        tl0picidx: Optional[int] = None,
        temporal_layer_id: Optional[int] = None,
        spatial_layer_id: Optional[int] = None,
        flexible_mode: bool = False,
        inter_layer_dependency: bool = False,
        scalability_structure_data_present: bool = False,
        reference_index_diff: Optional[List[int]] = None,
    ) -> None:
        self.start_of_layer_frame = start_of_layer_frame
        self.end_of_layer_frame = end_of_layer_frame
        self.picture_id = picture_id
        self.tl0picidx = tl0picidx
        self.temporal_layer_id = temporal_layer_id
        self.spatial_layer_id = spatial_layer_id
        self.flexible_mode = flexible_mode
        self.inter_layer_dependency = inter_layer_dependency
        self.scalability_structure_data_present = scalability_structure_data_present
        self.reference_index_diff = reference_index_diff or []

    def __bytes__(self) -> bytes:
        data = bytearray()
        octet = (self.start_of_layer_frame << 3) | (self.end_of_layer_frame << 2)
        
        if self.picture_id is not None:
            octet |= 1 << 7  # Picture ID present flag
        if self.flexible_mode:
            octet |= 1 << 4  # Flexible mode flag
        if self.scalability_structure_data_present:
            octet |= 1 << 1  # Scalability structure data present flag
        
        data.append(octet)

        if self.picture_id is not None:
            if self.picture_id < 128:
                data.append(self.picture_id)
            else:
                data.extend(pack("!H", (1 << 15) | self.picture_id))

        if self.temporal_layer_id is not None or self.spatial_layer_id is not None:
            layer_info = (self.temporal_layer_id << 5) | (self.spatial_layer_id << 1)
            if self.inter_layer_dependency:
                layer_info |= 1
            data.append(layer_info)

        if self.flexible_mode and self.reference_index_diff:
            for ref_diff in self.reference_index_diff:
                data.append((ref_diff << 1) | 1)
                
        # Apparently we can skip processing Scalibity Structures here so not doing currently.
        return bytes(data)

    def __repr__(self) -> str:
        return (
            f"Vp9PayloadDescriptor(S={self.start_of_layer_frame}, E={self.end_of_layer_frame}, "
            f"pic_id={self.picture_id}, TLID={self.temporal_layer_id}, SLID={self.spatial_layer_id})"
        )

    @classmethod
    def parse(cls, data: bytes) -> Tuple["Vp9PayloadDescriptor", bytes]:
        if len(data) < 1:
            raise ValueError("VP9 descriptor is too short")

        octet = data[0] # Read payload descriptor header
        picture_id_present = bool((octet >> 7) & 1)
        use_interpredict_frame = bool((octet >> 6) & 1)
        layer_indicies_present = bool((octet >> 5) & 1)
        flexible_mode = bool((octet >> 4) & 1)
        start_of_layer_frame = (octet >> 3) & 1
        end_of_layer_frame = (octet >> 2) & 1
        scalability_structure_data_present = bool((octet >> 1) & 1)
        pos = 1 # Start position at reading PID

        picture_id = None
        temporal_layer_id = None
        spatial_layer_id = None
        inter_layer_dependency = False
        reference_index_diff = []
        tl0picidx = None

        # Process PID
        if picture_id_present:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated Picture ID")

            if data[pos] & 0x80:
                if len(data) < pos + 2:
                    raise ValueError("VP9 descriptor has truncated long Picture ID")
                picture_id = unpack_from("!H", data, pos)[0] & 0x7FFF
                pos += 2
            else:
                picture_id = data[pos]
                pos += 1

        # Process layer indicies
        if layer_indicies_present:
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated layer indices")
            
            layer_octet = data[pos]
            temporal_layer_id = (layer_octet >> 5) & 0x07
            spatial_layer_id = (layer_octet >> 1) & 0x07
            inter_layer_dependency = bool(layer_octet & 0x01)
            pos += 1

            if not flexible_mode:
                if len(data) < pos + 1:
                    raise ValueError("VP9 descriptor has truncated TL0PICIDX")
                tl0picidx = data[pos]
                pos += 1
                
        if flexible_mode and use_interpredict_frame:
            while pos < len(data):
                ref_diff = data[pos] >> 1
                reference_index_diff.append(ref_diff)
                if not (data[pos] & 0x01):
                    break
                pos += 1
            
        # Scalability Structure Parsing
        if scalability_structure_data_present:
            scalability_structure = {}
            # Assume the first field indicates the number of layers
            if len(data) < pos + 1:
                raise ValueError("VP9 descriptor has truncated Scalability Structure")
            
            num_layers = ((data[pos] >> 5) & 0b111) # Read number of scalability layers, one is always present

            if num_layers == 0:
                num_layers = 1

            y = bool((data[pos] >> 3) & 1) 
            g = bool((data[pos] >> 4) & 1) 
            
            scalability_structure['num_layers'] = num_layers
            pos += 1

            if y:
                # Skip next two as resolution, W by H
                pos += 2

            pgfLoops = 1 # Default if g not set
            actualPgfLoops = 1
            if g:
                # Picture group present
                pgfLoops = data[pos] # This flag should dictate how many layers we need to process but acts more as a possible max,                
                pos += 1
            
            scalability_structure['layers'] = []
            for _ in range(pgfLoops):
                if len(data) < pos + 2:
                    raise ValueError("VP9 descriptor has truncated Scalability Structure for layers")
                
                layer_info = {
                    'layer_id': data[pos] >> 4,          # Example: Extract layer_id from the first 4 bits
                    'temporal_layer_id': data[pos] & 0x0F  # Example: Extract temporal layer ID from the last 4 bits
                }

                u = (data[pos] >> 2) & 0b1
                r = (data[pos] >> 1) & 0b1

                if r == 0: # If R is 0 then it defaults to having just one instance.
                    r = 1

                scalability_structure['layers'].append(layer_info)
                pos += 1  # Move to  pdiff

                continuation_flag = True # One segment can have multiple pdiffs based on cont flag
                while continuation_flag:
                    continuation_flag = bool(data[pos] & 0x01)
                    if continuation_flag:
                        pos += 1
                    else:
                        pos += 1
                        break

                pos += 1 # Move from final pdiff into payload
                break

        obj = cls( 
            start_of_layer_frame=start_of_layer_frame,
            end_of_layer_frame=end_of_layer_frame,
            picture_id=picture_id,
            tl0picidx=tl0picidx,
            temporal_layer_id=temporal_layer_id,
            spatial_layer_id=spatial_layer_id,
            flexible_mode=flexible_mode,
            inter_layer_dependency=inter_layer_dependency,
            scalability_structure_data_present=scalability_structure_data_present,
            reference_index_diff=reference_index_diff,
        )
        
        return obj, data[pos:]

def _vpx_assert(err: int) -> None:
    if err != lib.VPX_CODEC_OK:
        reason = ffi.string(lib.vpx_codec_err_to_string(err))
        raise Exception("libvpx error: " + reason.decode("utf8"))

class Vp8Decoder(Decoder):
    def __init__(self) -> None:
        self.codec = ffi.new("vpx_codec_ctx_t *")
        _vpx_assert(
            lib.vpx_codec_dec_init(self.codec, lib.vpx_codec_vp8_dx(), ffi.NULL, 0)
        )

        ppcfg = ffi.new("vp8_postproc_cfg_t *")
        ppcfg.post_proc_flag = lib.VP8_DEMACROBLOCK | lib.VP8_DEBLOCK
        ppcfg.deblocking_level = 3
        lib.vpx_codec_control_(self.codec, lib.VP8_SET_POSTPROC, ppcfg)

    def __del__(self) -> None:
        lib.vpx_codec_destroy(self.codec)

    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        frames: List[Frame] = []
        result = lib.vpx_codec_decode(
            self.codec,
            encoded_frame.data,
            len(encoded_frame.data),
            ffi.NULL,
            lib.VPX_DL_REALTIME,
        )
        if result == lib.VPX_CODEC_OK:
            it = ffi.new("vpx_codec_iter_t *")
            while True:
                img = lib.vpx_codec_get_frame(self.codec, it)
                if not img:
                    break
                assert img.fmt == lib.VPX_IMG_FMT_I420

                frame = VideoFrame(width=img.d_w, height=img.d_h)
                frame.pts = encoded_frame.timestamp
                frame.time_base = VIDEO_TIME_BASE

                for p in range(3):
                    i_stride = img.stride[p]
                    i_buf = ffi.buffer(img.planes[p], i_stride * img.d_h)
                    i_pos = 0

                    o_stride = frame.planes[p].line_size
                    o_buf = memoryview(cast(bytes, frame.planes[p]))
                    o_pos = 0

                    div = p and 2 or 1
                    for r in range(0, img.d_h // div):
                        o_buf[o_pos : o_pos + o_stride] = i_buf[
                            i_pos : i_pos + o_stride
                        ]
                        i_pos += i_stride
                        o_pos += o_stride

                frames.append(frame)

        return frames

class Vp9Decoder(Decoder):
    def __init__(self) -> None:
        self.codec = ffi.new("vpx_codec_ctx_t *")
        _vpx_assert(
            lib.vpx_codec_dec_init(self.codec, lib.vpx_codec_vp9_dx(), ffi.NULL, 0)
        )

    def __del__(self) -> None:
        lib.vpx_codec_destroy(self.codec)

    def decode(self, encoded_frame: JitterFrame) -> List[Frame]:
        frames: List[Frame] = []
        result = lib.vpx_codec_decode(
            self.codec,
            encoded_frame.data,
            len(encoded_frame.data),
            ffi.NULL,
            lib.VPX_DL_REALTIME,
        )
        if result == lib.VPX_CODEC_OK:
            it = ffi.new("vpx_codec_iter_t *")
            while True:
                img = lib.vpx_codec_get_frame(self.codec, it)
                if not img:
                    break
                assert img.fmt == lib.VPX_IMG_FMT_I420

                frame = VideoFrame(width=img.d_w, height=img.d_h)
                frame.pts = encoded_frame.timestamp
                frame.time_base = VIDEO_TIME_BASE

                for p in range(3):
                    i_stride = img.stride[p]
                    i_buf = ffi.buffer(img.planes[p], i_stride * img.d_h)
                    i_pos = 0

                    o_stride = frame.planes[p].line_size
                    o_buf = memoryview(cast(bytes, frame.planes[p]))
                    o_pos = 0

                    div = p and 2 or 1
                    for r in range(0, img.d_h // div):
                        o_buf[o_pos : o_pos + o_stride] = i_buf[
                            i_pos : i_pos + o_stride
                        ]
                        i_pos += i_stride
                        o_pos += o_stride

                frames.append(frame)

        return frames


class Vp8Encoder(Encoder):
    def __init__(self) -> None:
        self.cx = lib.vpx_codec_vp8_cx()

        self.cfg = ffi.new("vpx_codec_enc_cfg_t *")
        lib.vpx_codec_enc_config_default(self.cx, self.cfg, 0)

        self.buffer = bytearray(8000)
        self.codec = None
        self.picture_id = random.randint(0, (1 << 15) - 1)
        self.timestamp_increment = VIDEO_CLOCK_RATE // MAX_FRAME_RATE
        self.__target_bitrate = DEFAULT_BITRATE
        self.__update_config_needed = False

    def __del__(self) -> None:
        if self.codec:
            lib.vpx_codec_destroy(self.codec)

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        assert isinstance(frame, VideoFrame)
        if frame.format.name != "yuv420p":
            frame = frame.reformat(format="yuv420p")

        if self.codec and (frame.width != self.cfg.g_w or frame.height != self.cfg.g_h):
            lib.vpx_codec_destroy(self.codec)
            self.codec = None

        if not self.codec:
            # create codec
            self.codec = ffi.new("vpx_codec_ctx_t *")
            self.cfg.g_timebase.num = 1
            self.cfg.g_timebase.den = VIDEO_CLOCK_RATE
            self.cfg.g_lag_in_frames = 0
            self.cfg.g_threads = number_of_threads(
                frame.width * frame.height, multiprocessing.cpu_count()
            )
            self.cfg.g_w = frame.width
            self.cfg.g_h = frame.height
            self.cfg.rc_resize_allowed = 0
            self.cfg.rc_end_usage = lib.VPX_CBR
            self.cfg.rc_min_quantizer = 2
            self.cfg.rc_max_quantizer = 56
            self.cfg.rc_undershoot_pct = 100
            self.cfg.rc_overshoot_pct = 15
            self.cfg.rc_buf_initial_sz = 500
            self.cfg.rc_buf_optimal_sz = 600
            self.cfg.rc_buf_sz = 1000
            self.cfg.kf_mode = lib.VPX_KF_AUTO
            self.cfg.kf_max_dist = 3000
            self.__update_config()
            _vpx_assert(lib.vpx_codec_enc_init(self.codec, self.cx, self.cfg, 0))

            lib.vpx_codec_control_(
                self.codec, lib.VP8E_SET_NOISE_SENSITIVITY, ffi.cast("int", 4)
            )
            lib.vpx_codec_control_(
                self.codec, lib.VP8E_SET_STATIC_THRESHOLD, ffi.cast("int", 1)
            )
            lib.vpx_codec_control_(
                self.codec, lib.VP8E_SET_CPUUSED, ffi.cast("int", -6)
            )
            lib.vpx_codec_control_(
                self.codec,
                lib.VP8E_SET_TOKEN_PARTITIONS,
                ffi.cast("int", lib.VP8_ONE_TOKENPARTITION),
            )

            # create image on a dummy buffer, we will fill the pointers during encoding
            self.image = ffi.new("vpx_image_t *")
            lib.vpx_img_wrap(
                self.image,
                lib.VPX_IMG_FMT_I420,
                frame.width,
                frame.height,
                1,
                ffi.cast("void*", 1),
            )
        elif self.__update_config_needed:
            self.__update_config()
            _vpx_assert(lib.vpx_codec_enc_config_set(self.codec, self.cfg))

        # setup image
        for p in range(3):
            self.image.planes[p] = ffi.cast("void*", frame.planes[p].buffer_ptr)
            self.image.stride[p] = frame.planes[p].line_size

        # encode frame
        flags = 0
        if force_keyframe:
            flags |= lib.VPX_EFLAG_FORCE_KF
        _vpx_assert(
            lib.vpx_codec_encode(
                self.codec,
                self.image,
                frame.pts,
                self.timestamp_increment,
                flags,
                lib.VPX_DL_REALTIME,
            )
        )

        it = ffi.new("vpx_codec_iter_t *")
        length = 0
        while True:
            pkt = lib.vpx_codec_get_cx_data(self.codec, it)
            if not pkt:
                break
            elif pkt.kind == lib.VPX_CODEC_CX_FRAME_PKT:
                # resize buffer if needed
                if length + pkt.data.frame.sz > len(self.buffer):
                    new_buffer = bytearray(length + pkt.data.frame.sz)
                    new_buffer[0:length] = self.buffer[0:length]
                    self.buffer = new_buffer

                # append new data
                self.buffer[length : length + pkt.data.frame.sz] = ffi.buffer(
                    pkt.data.frame.buf, pkt.data.frame.sz
                )
                length += pkt.data.frame.sz

        # packetize
        payloads = self._packetize(self.buffer[:length], self.picture_id)
        timestamp = convert_timebase(frame.pts, frame.time_base, VIDEO_TIME_BASE)
        self.picture_id = (self.picture_id + 1) % (1 << 15)
        return payloads, timestamp

    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
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
        if bitrate != self.__target_bitrate:
            self.__target_bitrate = bitrate
            self.__update_config_needed = True

    @classmethod
    def _packetize(cls, buffer: bytes, picture_id: int) -> List[bytes]:
        payloads = []
        descr = VP8PayloadDescriptor(
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

    def __update_config(self) -> None:
        self.cfg.rc_target_bitrate = self.__target_bitrate // 1000
        self.__update_config_needed = False

class Vp9Encoder(Encoder):
    def __init__(self) -> None:
        self.cx = lib.vpx_codec_vp9_cx()

        self.cfg = ffi.new("vpx_codec_enc_cfg_t *")
        lib.vpx_codec_enc_config_default(self.cx, self.cfg, 0)

        self.buffer = bytearray(8000)
        self.codec = None
        self.picture_id = random.randint(0, (1 << 15) - 1)
        self.timestamp_increment = VIDEO_CLOCK_RATE // MAX_FRAME_RATE
        self.__target_bitrate = DEFAULT_BITRATE
        self.__update_config_needed = False

    def __del__(self) -> None:
        if self.codec:
            lib.vpx_codec_destroy(self.codec)

    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> Tuple[List[bytes], int]:
        assert isinstance(frame, VideoFrame)
                
        if frame.format.name != "yuv420p":
            frame = frame.reformat(format="yuv420p")

        if self.codec and (frame.width != self.cfg.g_w or frame.height != self.cfg.g_h):
            lib.vpx_codec_destroy(self.codec)
            self.codec = None

        if not self.codec:
            # create codec
            self.codec = ffi.new("vpx_codec_ctx_t *")
            self.cfg.g_timebase.num = 1
            self.cfg.g_timebase.den = VIDEO_CLOCK_RATE
            self.cfg.g_lag_in_frames = 0
            self.cfg.g_threads = number_of_threads(
                frame.width * frame.height, multiprocessing.cpu_count()
            )
            self.cfg.g_w = frame.width
            self.cfg.g_h = frame.height
            self.cfg.rc_resize_allowed = 0
            self.cfg.rc_end_usage = lib.VPX_CBR
            self.cfg.rc_min_quantizer = 2
            self.cfg.rc_max_quantizer = 56
            self.cfg.rc_undershoot_pct = 100
            self.cfg.rc_overshoot_pct = 15
            self.cfg.rc_buf_initial_sz = 500
            self.cfg.rc_buf_optimal_sz = 600
            self.cfg.rc_buf_sz = 1000
            self.cfg.kf_mode = lib.VPX_KF_AUTO
            self.cfg.kf_max_dist = 3000
            self.__update_config()
            _vpx_assert(lib.vpx_codec_enc_init(self.codec, self.cx, self.cfg, 0))

            # Set encoder options
            lib.vpx_codec_control_(
                self.codec, lib.VP9E_SET_NOISE_SENSITIVITY, ffi.cast("int", 1)
            )
            lib.vpx_codec_control_(
                self.codec, lib.VP8E_SET_STATIC_THRESHOLD, ffi.cast("int", 1)
            )
            lib.vpx_codec_control_(
                self.codec, lib.VP8E_SET_CPUUSED, ffi.cast("int", -4)
            )

            # create image on a dummy buffer, we will fill the pointers during encoding
            self.image = ffi.new("vpx_image_t *")
            lib.vpx_img_wrap(
                self.image,
                lib.VPX_IMG_FMT_I420,
                frame.width,
                frame.height,
                1,
                ffi.cast("void*", 1),
            )
        elif self.__update_config_needed:
            self.__update_config()
            _vpx_assert(lib.vpx_codec_enc_config_set(self.codec, self.cfg))

        # setup image
        for p in range(3):
            self.image.planes[p] = ffi.cast("void*", frame.planes[p].buffer_ptr)
            self.image.stride[p] = frame.planes[p].line_size

        # encode frame
        flags = 0
        if force_keyframe:
            flags |= lib.VPX_EFLAG_FORCE_KF
        _vpx_assert(
            lib.vpx_codec_encode(
                self.codec,
                self.image,
                frame.pts,
                self.timestamp_increment,
                flags,
                lib.VPX_DL_REALTIME,
            )
        )
        
        it = ffi.new("vpx_codec_iter_t *")
        length = 0
        while True:
            pkt = lib.vpx_codec_get_cx_data(self.codec, it)
            if not pkt:
                break
            elif pkt.kind == lib.VPX_CODEC_CX_FRAME_PKT:
                # resize buffer if needed
                if length + pkt.data.frame.sz > len(self.buffer):
                    new_buffer = bytearray(length + pkt.data.frame.sz)
                    new_buffer[0:length] = self.buffer[0:length]
                    self.buffer = new_buffer

                # append new data
                self.buffer[length : length + pkt.data.frame.sz] = ffi.buffer(
                    pkt.data.frame.buf, pkt.data.frame.sz
                )
                length += pkt.data.frame.sz

        # packetize
        payloads = self._packetize_vp9(self.buffer[:length], self.picture_id)
        timestamp = convert_timebase(frame.pts, frame.time_base, VIDEO_TIME_BASE)
        self.picture_id = (self.picture_id + 1) % (1 << 15)
        return payloads, timestamp

    def pack(self, packet: Packet) -> Tuple[List[bytes], int]:
        payloads = self._packetize_vp9(bytes(packet), self.picture_id)
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
        if bitrate != self.__target_bitrate:
            self.__target_bitrate = bitrate
            self.__update_config_needed = True

    @classmethod
    def _packetize_vp9(cls, buffer: bytes, picture_id: int) -> List[bytes]:
        payloads = []
        descr = Vp9PayloadDescriptor(
            start_of_layer_frame=1,
            end_of_layer_frame=0,
            picture_id=picture_id
        )
        length = len(buffer)
        pos = 0
        while pos < length:
            descr_bytes = bytes(descr)
            size = min(length - pos, PACKET_MAX - len(descr_bytes))
            payloads.append(descr_bytes + buffer[pos : pos + size])
            pos += size
        return payloads

    def __update_config(self) -> None:
        self.cfg.rc_target_bitrate = self.__target_bitrate // 1000
        self.__update_config_needed = False


def vp8_depayload(payload: bytes) -> bytes:
    descriptor, data = VP8PayloadDescriptor.parse(payload)
    return data

def vp9_depayload(payload: bytes) -> bytes:
    descriptor, data = Vp9PayloadDescriptor.parse(payload)    
    return data

