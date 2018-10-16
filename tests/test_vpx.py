import fractions
from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.vpx import (Vp8Decoder, Vp8Encoder, VpxPayloadDescriptor,
                               _vpx_assert, number_of_threads)
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .codecs import CodecTestCase

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


class Vp8Test(CodecTestCase):
    def test_assert(self):
        with self.assertRaises(Exception) as cm:
            _vpx_assert(1)
        self.assertEqual(str(cm.exception), 'libvpx error: Unspecified internal error')

    def test_decoder(self):
        decoder = get_decoder(VP8_CODEC)
        self.assertTrue(isinstance(decoder, Vp8Decoder))

    def test_encoder(self):
        encoder = get_encoder(VP8_CODEC)
        self.assertTrue(isinstance(encoder, Vp8Encoder))

        frame = self.create_video_frame(width=640, height=480, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

        # change resolution
        frame = self.create_video_frame(width=320, height=240, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 3000)

    def test_encoder_rgb(self):
        encoder = get_encoder(VP8_CODEC)
        self.assertTrue(isinstance(encoder, Vp8Encoder))

        frame = self.create_video_frame(width=640, height=480, pts=0, format='rgb24')
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 0)

    def test_encoder_large(self):
        encoder = get_encoder(VP8_CODEC)
        self.assertTrue(isinstance(encoder, Vp8Encoder))

        # first keyframe
        frame = self.create_video_frame(width=2560, height=1920, pts=0)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 7)
        self.assertEqual(len(payloads[0]), 1300)
        self.assertEqual(timestamp, 0)

        # delta frame
        frame = self.create_video_frame(width=2560, height=1920, pts=3000)
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        self.assertTrue(len(payloads[0]) < 1300)
        self.assertEqual(timestamp, 3000)

        # force keyframe
        frame = self.create_video_frame(width=2560, height=1920, pts=6000)
        payloads, timestamp = encoder.encode(frame, force_keyframe=True)
        self.assertEqual(len(payloads), 7)
        self.assertEqual(len(payloads[0]), 1300)
        self.assertEqual(timestamp, 6000)

    def test_number_of_threads(self):
        self.assertEqual(number_of_threads(1920 * 1080, 16), 8)
        self.assertEqual(number_of_threads(1920 * 1080, 8), 3)
        self.assertEqual(number_of_threads(1920 * 1080, 4), 2)
        self.assertEqual(number_of_threads(1920 * 1080, 2), 1)

    def test_roundtrip_1280_720(self):
        self.roundtrip_video(VP8_CODEC, 1280, 720)

    def test_roundtrip_960_540(self):
        self.roundtrip_video(VP8_CODEC, 960, 540)

    def test_roundtrip_640_480(self):
        self.roundtrip_video(VP8_CODEC, 640, 480)

    def test_roundtrip_640_480_time_base(self):
        self.roundtrip_video(VP8_CODEC, 640, 480, time_base=fractions.Fraction(1, 9000))

    def test_roundtrip_320_240(self):
        self.roundtrip_video(VP8_CODEC, 320, 240)
