from unittest import TestCase

from aiortc.rtp import RtcpPacket, RtpPacket

from .utils import load


class RtcpPacketTest(TestCase):
    def test_sender_report(self):
        data = load('rtcp_sr.bin')
        packet = RtcpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.packet_type, 200)
        self.assertEqual(packet.ssrc, 1831097322)
        self.assertEqual(packet.sender_info.ntp_timestamp, 16016567581311369308)
        self.assertEqual(packet.sender_info.rtp_timestamp, 1722342718)
        self.assertEqual(packet.sender_info.packet_count, 269)
        self.assertEqual(packet.sender_info.octet_count, 13557)

    def test_receiver_report(self):
        data = load('rtcp_rr.bin')
        packet = RtcpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.packet_type, 201)
        self.assertEqual(packet.ssrc, 817267719)

    def test_truncated(self):
        data = load('rtcp_rr.bin')[0:7]
        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), 'RTCP packet length is less than 8 bytes')

    def test_bad_version(self):
        data = b'\xc0' + load('rtcp_rr.bin')[1:]
        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), 'RTCP packet has invalid version')


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
                         'RtpPacket(seq=15743, ts=3937035252, marker=0, payload=0, 160 bytes)')

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
