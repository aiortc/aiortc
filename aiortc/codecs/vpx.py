import struct

from ..mediastreams import VideoFrame
from ._vpx import ffi, lib


class VpxPayloadDescriptor:
    props = ['partition_start', 'partition_id', 'picture_id']

    def __init__(self, partition_start, partition_id, picture_id=None):
        self.partition_start = partition_start
        self.partition_id = partition_id
        self.picture_id = picture_id

    def __bytes__(self):
        octet = (self.partition_start << 4) | self.partition_id
        if self.picture_id is not None:
            ext_octet = 1 << 7
            data = struct.pack('!BB', (1 << 7) | octet, ext_octet)
            if self.picture_id < 128:
                data += struct.pack('!B', self.picture_id)
            else:
                data += struct.pack('!H', (1 << 15) | self.picture_id)
            return data
        else:
            return struct.pack('!B', octet)

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
                    picture_id = struct.unpack('!H', data[pos:pos+2])[0] & 0x7fff
                    pos += 2
                else:
                    picture_id = data[pos]
                    pos += 1

            # unused
            if ext_L:
                pos += 1
            if ext_T or ext_K:
                pos += 1

        obj = cls(partition_start=partition_start, partition_id=partition_id, picture_id=picture_id)
        return obj, data[pos:]


def _vpx_assert(err):
    if err != lib.VPX_CODEC_OK:
        reason = ffi.string(lib.vpx_codec_err_to_string(err))
        raise Exception('Failed: ' + reason.decode('utf8'))


class VpxDecoder:
    def __init__(self):
        self.codec = ffi.new('vpx_codec_ctx_t *')
        _vpx_assert(lib.vpx_codec_dec_init(self.codec, lib.vpx_codec_vp8_dx(), ffi.NULL, 0))

    def __del__(self):
        lib.vpx_codec_destroy(self.codec)

    def decode(self, data):
        """
        _vpx_assert(lib.vpx_codec_decode(
            self.codec, data, len(data), ffi.NULL, lib.VPX_DL_REALTIME))
        """
        # TODO : actually decode data!
        return VideoFrame(width=320, height=240, data=b'\x00' * 115200)


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
            self.cfg.g_w = frame.width
            self.cfg.g_h = frame.height

            self.codec = ffi.new('vpx_codec_ctx_t *')
            _vpx_assert(lib.vpx_codec_enc_init(self.codec, self.cx, self.cfg, 0))

        _vpx_assert(lib.vpx_codec_encode(
            self.codec, image, self.frame_count, 1,  0, lib.VPX_DL_REALTIME))
        self.frame_count += 1

        it = ffi.new('vpx_codec_iter_t *')
        pkt = lib.vpx_codec_get_cx_data(self.codec, it)
        assert pkt

        descr = VpxPayloadDescriptor(partition_start=1, partition_id=0)
        return bytes(descr) + ffi.buffer(pkt.data.frame.buf, pkt.data.frame.sz)
