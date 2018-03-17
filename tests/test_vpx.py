from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.vpx import (VpxDecoder, VpxEncoder, VpxPayloadDescriptor,
                               _vpx_assert)
from aiortc.mediastreams import VideoFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

VP8_CODEC = RTCRtpCodecParameters(name='VP8', clockRate=90000)


class VpxPayloadDescriptorTest(TestCase):
    def test_no_picture_id(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x10')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x10')
        self.assertEqual(repr(descr), 'VpxPayloadDescriptor(S=1, PID=0, pic_id=None)')

        self.assertEqual(rest, b'')

    def test_short_picture_id_17(self):
        """
        From RFC 7741 - 4.6.3
        """
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x11')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 17)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x90\x80\x11')
        self.assertEqual(repr(descr), 'VpxPayloadDescriptor(S=1, PID=0, pic_id=17)')

        self.assertEqual(rest, b'')

    def test_short_picture_id_127(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x7f')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 127)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x90\x80\x7f')

        self.assertEqual(rest, b'')

    def test_long_picture_id_128(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x80\x80')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 128)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x90\x80\x80\x80')

        self.assertEqual(rest, b'')

    def test_long_picture_id_4711(self):
        """
        From RFC 7741 - 4.6.5
        """
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x92\x67')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 4711)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x90\x80\x92\x67')

        self.assertEqual(rest, b'')

    def test_tl0picidx(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\xc0\x92\x67\x81')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 4711)
        self.assertEqual(descr.tl0picidx, 129)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x90\xc0\x92\x67\x81')

        self.assertEqual(rest, b'')

    def test_tid(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x20\xe0')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, (3, 1))
        self.assertEqual(descr.keyidx, None)
        self.assertEqual(bytes(descr), b'\x90\x20\xe0')

        self.assertEqual(rest, b'')

    def test_keyidx(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x10\x1f')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(descr.tl0picidx, None)
        self.assertEqual(descr.tid, None)
        self.assertEqual(descr.keyidx, 31)
        self.assertEqual(bytes(descr), b'\x90\x10\x1f')

        self.assertEqual(rest, b'')


class Vp8Test(TestCase):
    def test_assert(self):
        with self.assertRaises(Exception) as cm:
            _vpx_assert(1)
        self.assertEqual(str(cm.exception), 'libvpx error: Unspecified internal error')

    def test_decoder(self):
        decoder = get_decoder(VP8_CODEC)
        self.assertTrue(isinstance(decoder, VpxDecoder))

    def test_encoder(self):
        encoder = get_encoder(VP8_CODEC)
        self.assertTrue(isinstance(encoder, VpxEncoder))

        frame = VideoFrame(width=640, height=480)
        payloads = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)

        frame = VideoFrame(width=320, height=240)
        payloads = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)

    def test_encoder_large(self):
        encoder = get_encoder(VP8_CODEC)
        self.assertTrue(isinstance(encoder, VpxEncoder))

        frame = VideoFrame(width=2560, height=1920)
        payloads = encoder.encode(frame)
        self.assertEqual(len(payloads), 7)
        self.assertEqual(len(payloads[0]), 1300)
