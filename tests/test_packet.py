import binascii
from unittest import TestCase

from aioquic.packet import pull_quic_header
from aioquic.tls import Buffer, BufferReadError

from .utils import load


class PacketTest(TestCase):
    def test_parse_empty(self):
        buf = Buffer(data=b'')
        with self.assertRaises(BufferReadError):
            pull_quic_header(buf)

    def test_parse_initial_client(self):
        buf = Buffer(data=load('initial_client.bin'))
        header = pull_quic_header(buf)
        self.assertEqual(header.version, 0xff000011)
        self.assertEqual(header.destination_cid, binascii.unhexlify('90ed1e1c7b04b5d3'))
        self.assertEqual(header.source_cid, b'')
        self.assertEqual(header.token, b'')
        self.assertEqual(buf.tell(), 17)

    def test_parse_initial_server(self):
        buf = Buffer(data=load('initial_server.bin'))
        header = pull_quic_header(buf)
        self.assertEqual(header.version, 0xff000011)
        self.assertEqual(header.destination_cid, b'')
        self.assertEqual(header.source_cid, binascii.unhexlify('0fcee9852fde8780'))
        self.assertEqual(header.token, b'')
        self.assertEqual(buf.tell(), 17)

    def test_parse_long_header_no_fixed_bit(self):
        buf = Buffer(data=b'\x80\x00\x00\x00\x00\x00')
        with self.assertRaises(ValueError) as cm:
            pull_quic_header(buf)
        self.assertEqual(str(cm.exception), 'Packet fixed bit is zero')

    def test_parse_long_header_too_short(self):
        buf = Buffer(data=b'\xc0\x00')
        with self.assertRaises(BufferReadError):
            pull_quic_header(buf)

    def test_parse_short_header(self):
        buf = Buffer(data=b'\x40\x00')
        with self.assertRaises(ValueError) as cm:
            pull_quic_header(buf)
        self.assertEqual(str(cm.exception), 'Short header is not supported yet')
