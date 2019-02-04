import binascii
from unittest import TestCase

from aioquic.packet import QuicHeader, unpack_variable_length

from .utils import load


class UtilTest(TestCase):
    def test_unpack_variable_length(self):
        # 1 byte
        self.assertEqual(unpack_variable_length(b'\x00'), (0, 1))
        self.assertEqual(unpack_variable_length(b'\x01'), (1, 1))
        self.assertEqual(unpack_variable_length(b'\x25'), (37, 1))
        self.assertEqual(unpack_variable_length(b'\x3f'), (63, 1))

        # 2 bytes
        self.assertEqual(unpack_variable_length(b'\x7b\xbd'), (15293, 2))
        self.assertEqual(unpack_variable_length(b'\x7f\xff'), (16383, 2))

        # 4 bytes
        self.assertEqual(unpack_variable_length(b'\x9d\x7f\x3e\x7d'), (494878333, 4))
        self.assertEqual(unpack_variable_length(b'\xbf\xff\xff\xff'), (1073741823, 4))

        # 8 bytes
        self.assertEqual(unpack_variable_length(b'\xc2\x19\x7c\x5e\xff\x14\xe8\x8c'),
                         (151288809941952652, 8))
        self.assertEqual(unpack_variable_length(b'\xff\xff\xff\xff\xff\xff\xff\xff'),
                         (4611686018427387903, 8))


class PacketTest(TestCase):
    def test_parse_initial_client(self):
        data = load('initial_client.bin')
        header = QuicHeader.parse(data)
        self.assertEqual(header.version, 0xff000011)
        self.assertEqual(header.destination_cid, binascii.unhexlify('90ed1e1c7b04b5d3'))
        self.assertEqual(header.source_cid, b'')
        self.assertEqual(header.token, b'')

    def test_parse_initial_server(self):
        data = load('initial_server.bin')
        header = QuicHeader.parse(data)
        self.assertEqual(header.version, 0xff000011)
        self.assertEqual(header.destination_cid, b'')
        self.assertEqual(header.source_cid, binascii.unhexlify('0fcee9852fde8780'))
        self.assertEqual(header.token, b'')

    def test_parse_long_header_bad_packet_type(self):
        with self.assertRaises(ValueError) as cm:
            QuicHeader.parse(b'\x80\x00\x00\x00\x00\x00')
        self.assertEqual(str(cm.exception), 'Long header packet type 0x80 is not supported')

    def test_parse_long_header_too_short(self):
        with self.assertRaises(ValueError) as cm:
            QuicHeader.parse(b'\x80\x00')
        self.assertEqual(str(cm.exception), 'Long header is too short (2 bytes)')

    def test_parse_short_header(self):
        with self.assertRaises(ValueError) as cm:
            QuicHeader.parse(b'\x00\x00')
        self.assertEqual(str(cm.exception), 'Short header is not supported yet')

    def test_parse_too_short_header(self):
        with self.assertRaises(ValueError) as cm:
            QuicHeader.parse(b'\x00')
        self.assertEqual(str(cm.exception), 'Packet is too short (1 bytes)')
