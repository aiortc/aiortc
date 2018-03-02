from unittest import TestCase

from aiortc.codecs.vpx import VpxPayloadDescriptor


class VpxPayloadDescriptorTest(TestCase):
    def test_no_picture_id(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x10')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, None)
        self.assertEqual(bytes(descr), b'\x10')

        self.assertEqual(rest, b'')

    def test_short_picture_id_17(self):
        descr, rest = VpxPayloadDescriptor.parse(b'\x90\x80\x11')
        self.assertEqual(descr.partition_start, 1)
        self.assertEqual(descr.partition_id, 0)
        self.assertEqual(descr.picture_id, 17)
        self.assertEqual(bytes(descr), b'\x90\x80\x11')

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
