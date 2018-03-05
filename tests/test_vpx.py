from unittest import TestCase

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.vpx import VpxDecoder, VpxEncoder, VpxPayloadDescriptor
from aiortc.rtp import Codec

VP8_CODEC = Codec(kind='video', name='VP8', clockrate=90000)


class VpxPayloadDescriptorTest(TestCase):
    def test_no_picture_id(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x10')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(bytes(descr), b'\x10')
        self.assertEqual(repr(descr), 'VpxPayloadDescriptor(S=1, PID=0, pic_id=None)')

        self.assertEqual(rest, b'')

    def test_short_picture_id_17(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x11')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 17)
        self.assertEqual(bytes(descr), b'\x90\x80\x11')
        self.assertEqual(repr(descr), 'VpxPayloadDescriptor(S=1, PID=0, pic_id=17)')

        self.assertEqual(rest, b'')

    def test_short_picture_id_127(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x7f')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 127)
        self.assertEqual(bytes(descr), b'\x90\x80\x7f')

        self.assertEqual(rest, b'')

    def test_long_picture_id_128(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x80\x80')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 128)
        self.assertEqual(bytes(descr), b'\x90\x80\x80\x80')

        self.assertEqual(rest, b'')

    def test_long_picture_id_384(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x81\x80')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 384)
        self.assertEqual(bytes(descr), b'\x90\x80\x81\x80')

        self.assertEqual(rest, b'')


class Vp8Test(TestCase):
    def test_decoder(self):
        decoder = get_decoder(VP8_CODEC)
        self.assertTrue(isinstance(decoder, VpxDecoder))

    def test_encoder(self):
        encoder = get_encoder(VP8_CODEC)
        self.assertTrue(isinstance(encoder, VpxEncoder))
