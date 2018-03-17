import math
from struct import pack, unpack

from ..mediastreams import VideoFrame
from ._vpx import ffi, lib

PACKET_MAX = 1300 - 1


class VpxPayloadDescriptor:
    props = ['partition_start', 'partition_id', 'picture_id']

    def __init__(self, partition_start, partition_id, picture_id=None,
                 tl0picidx=None, tid=None, keyidx=None):
        self.partition_start = partition_start
        self.partition_id = partition_id
        self.picture_id = picture_id
        self.tl0picidx = tl0picidx
        self.tid = tid
        self.keyidx = keyidx

    def __bytes__(self):
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
            data = pack('!BB', (1 << 7) | octet, ext_octet)
            if self.picture_id is not None:
                if self.picture_id < 128:
                    data += pack('!B', self.picture_id)
                else:
                    data += pack('!H', (1 << 15) | self.picture_id)
            if self.tl0picidx is not None:
                data += pack('!B', self.tl0picidx)
            if self.tid is not None or self.keyidx is not None:
                t_k = 0
                if self.tid is not None:
                    t_k |= (self.tid[0] << 6) | (self.tid[1] << 5)
                if self.keyidx is not None:
                    t_k |= self.keyidx
                data += pack('!B', t_k)
        else:
            data = pack('!B', octet)

        return data

    def __repr__(self):
        return 'VpxPayloadDescriptor(S=%d, PID=%d, pic_id=%s)' % (
            self.partition_start, self.partition_id, self.picture_id)

    @classmethod
    def parse(cls, data):
        # first byte
        octet = data[0]
        extended = octet >> 7
        partition_start = (octet >> 4) & 1
        partition_id = octet & 0xf
        picture_id = None
        tl0picidx = None
        tid = None
        keyidx = None
        pos = 1

        # extended control bits
        if extended:
            octet = data[pos]
            ext_I = (octet >> 7) & 1
            ext_L = (octet >> 6) & 1
            ext_T = (octet >> 5) & 1
            ext_K = (octet >> 4) & 1
            pos += 1

            # picture id
            if ext_I:
                if data[pos] & 0x80:
                    picture_id = unpack('!H', data[pos:pos+2])[0] & 0x7fff
                    pos += 2
                else:
                    picture_id = data[pos]
                    pos += 1

            # unused
            if ext_L:
                tl0picidx = unpack('!B', data[pos:pos+1])[0]
                pos += 1
            if ext_T or ext_K:
                t_k = unpack('!B', data[pos:pos+1])[0]
                if ext_T:
                    tid = (
                        (t_k >> 6) & 3,
                        (t_k >> 5) & 1
                    )
                if ext_K:
                    keyidx = t_k & 0x1f
                pos += 1

        obj = cls(partition_start=partition_start, partition_id=partition_id, picture_id=picture_id,
                  tl0picidx=tl0picidx, tid=tid, keyidx=keyidx)
        return obj, data[pos:]


def _vpx_assert(err):
    if err != lib.VPX_CODEC_OK:
        reason = ffi.string(lib.vpx_codec_err_to_string(err))
        raise Exception('libvpx error: ' + reason.decode('utf8'))


class VpxDecoder:
    def __init__(self):
        self.codec = ffi.new('vpx_codec_ctx_t *')
        _vpx_assert(lib.vpx_codec_dec_init(self.codec, lib.vpx_codec_vp8_dx(), ffi.NULL, 0))

    def __del__(self):
        lib.vpx_codec_destroy(self.codec)

    def decode(self, *payloads):
        data = b''
        for payload in payloads:
            if payload:
                vpx_descriptor, rest = VpxPayloadDescriptor.parse(payload)
                data += rest

        frames = []
        result = lib.vpx_codec_decode(self.codec, data, len(data), ffi.NULL, lib.VPX_DL_REALTIME)
        if result == lib.VPX_CODEC_OK:
            it = ffi.new('vpx_codec_iter_t *')
            while True:
                img = lib.vpx_codec_get_frame(self.codec, it)
                if not img:
                    break
                assert img.fmt == lib.VPX_IMG_FMT_I420

                o_buf = bytearray(math.ceil(img.d_w * img.d_h * 12 / 8))
                o_pos = 0
                for p in range(3):
                    i_stride = img.stride[p]
                    i_buf = ffi.buffer(img.planes[p], i_stride * img.d_h)
                    i_pos = 0

                    div = p and 2 or 1
                    o_stride = img.d_w // div
                    for r in range(0, img.d_h // div):
                        o_buf[o_pos:o_pos + o_stride] = i_buf[i_pos:i_pos + i_stride]
                        i_pos += i_stride
                        o_pos += o_stride

                frames.append(VideoFrame(width=img.d_w, height=img.d_h, data=bytes(o_buf)))

        return frames


class VpxEncoder:
    timestamp_increment = 1

    def __init__(self):
        self.cx = lib.vpx_codec_vp8_cx()

        self.cfg = ffi.new('vpx_codec_enc_cfg_t *')
        lib.vpx_codec_enc_config_default(self.cx, self.cfg, 0)

        self.codec = None
        self.frame_count = 0

    def __del__(self):
        if self.codec:
            lib.vpx_codec_destroy(self.codec)

    def encode(self, frame):
        image = ffi.new('vpx_image_t *')

        lib.vpx_img_wrap(image, lib.VPX_IMG_FMT_I420,
                         frame.width, frame.height, 1, frame.data)

        if not self.codec:
            self.codec = ffi.new('vpx_codec_ctx_t *')
            self.cfg.g_w = frame.width
            self.cfg.g_h = frame.height
            _vpx_assert(lib.vpx_codec_enc_init(self.codec, self.cx, self.cfg, 0))
        elif frame.width != self.cfg.g_w or frame.height != self.cfg.g_h:
            self.cfg.g_w = frame.width
            self.cfg.g_h = frame.height
            _vpx_assert(lib.vpx_codec_enc_config_set(self.codec, self.cfg))

        _vpx_assert(lib.vpx_codec_encode(
            self.codec, image, self.frame_count, 1,  0, lib.VPX_DL_REALTIME))
        self.frame_count += 1

        it = ffi.new('vpx_codec_iter_t *')
        payloads = []
        while True:
            pkt = lib.vpx_codec_get_cx_data(self.codec, it)
            if not pkt:
                break
            if pkt and pkt.kind == lib.VPX_CODEC_CX_FRAME_PKT:
                buf = ffi.buffer(pkt.data.frame.buf, pkt.data.frame.sz)
                descr = VpxPayloadDescriptor(partition_start=1, partition_id=0)
                for pos in range(0, len(buf), PACKET_MAX):
                    data = buf[pos:pos + PACKET_MAX]
                    payloads.append(bytes(descr) + data)
                    descr.partition_start = 0
        return payloads
