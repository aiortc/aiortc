from unittest import TestCase

from aiortc.rtp import RtpPacket

from .utils import load


class RtpPacketTest(TestCase):
    def test_no_ssrc(self):
        data = load('rtp.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.extension, 0)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 0)
        self.assertEqual(packet.sequence_number, 15743)
        self.assertEqual(packet.timestamp, 3937035252)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(len(packet.payload), 160)
        self.assertEqual(bytes(packet), data)

        self.assertEqual(repr(packet),
                         'Packet(seq=15743, ts=3937035252, marker=0, payload=0, 160 bytes)')

    def test_padding_only(self):
        data = load('rtp_only_padding.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.extension, 0)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 120)
        self.assertEqual(packet.sequence_number, 27759)
        self.assertEqual(packet.timestamp, 4044047131)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(len(packet.payload), 0)
        self.assertEqual(bytes(packet), b'\x80' + data[1:12])

    def test_padding_too_long(self):
        data = load('rtp_only_padding.bin')[0:12] + b'\x02'
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), 'RTP packet padding length is invalid')

    def test_padding_zero(self):
        data = load('rtp_only_padding.bin')[0:12] + b'\x00'
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), 'RTP packet padding length is invalid')

    def test_with_csrc(self):
        data = load('rtp_with_csrc.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.extension, 0)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 0)
        self.assertEqual(packet.sequence_number, 16082)
        self.assertEqual(packet.timestamp, 144)
        self.assertEqual(packet.csrc, [2882400001, 3735928559])
        self.assertEqual(len(packet.payload), 160)
        self.assertEqual(bytes(packet), data)

    def test_truncated(self):
        data = load('rtp.bin')[0:11]
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), 'RTP packet length is less than 12 bytes')

    def test_bad_version(self):
        data = b'\xc0' + load('rtp.bin')[1:]
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), 'RTP packet has invalid version')
