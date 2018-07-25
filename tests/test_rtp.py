import datetime
from unittest import TestCase

from aiortc.rtp import (RtcpByePacket, RtcpPacket, RtcpRrPacket,
                        RtcpSdesPacket, RtcpSrPacket, RtpPacket,
                        clamp_packets_lost, datetime_from_ntp, datetime_to_ntp,
                        get_header_extensions, pack_packets_lost, seq_gt,
                        seq_plus_one, set_header_extensions,
                        unpack_packets_lost)

from .utils import load


class RtcpPacketTest(TestCase):
    def test_bye(self):
        data = load('rtcp_bye.bin')
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = packets[0]
        self.assertTrue(isinstance(packet, RtcpByePacket))
        self.assertEqual(packet.sources, [2924645187])
        self.assertEqual(bytes(packet), data)

        self.assertEqual(repr(packet), 'RtcpByePacket(sources=[2924645187])')

    def test_rr(self):
        data = load('rtcp_rr.bin')
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = packets[0]
        self.assertTrue(isinstance(packet, RtcpRrPacket))
        self.assertEqual(packet.ssrc, 817267719)
        self.assertEqual(packet.reports[0].ssrc, 1200895919)
        self.assertEqual(packet.reports[0].fraction_lost, 0)
        self.assertEqual(packet.reports[0].packets_lost, 0)
        self.assertEqual(packet.reports[0].highest_sequence, 630)
        self.assertEqual(packet.reports[0].jitter, 1906)
        self.assertEqual(packet.reports[0].lsr, 0)
        self.assertEqual(packet.reports[0].dlsr, 0)
        self.assertEqual(bytes(packet), data)

    def test_sdes(self):
        data = load('rtcp_sdes.bin')
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = packets[0]
        self.assertTrue(isinstance(packet, RtcpSdesPacket))
        self.assertEqual(packet.chunks[0].ssrc, 1831097322)
        self.assertEqual(packet.chunks[0].items, [
            (1, b'{63f459ea-41fe-4474-9d33-9707c9ee79d1}'),
        ])
        self.assertEqual(bytes(packet), data)

    def test_sr(self):
        data = load('rtcp_sr.bin')
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = packets[0]
        self.assertTrue(isinstance(packet, RtcpSrPacket))
        self.assertEqual(packet.ssrc, 1831097322)
        self.assertEqual(packet.sender_info.ntp_timestamp, 16016567581311369308)
        self.assertEqual(packet.sender_info.rtp_timestamp, 1722342718)
        self.assertEqual(packet.sender_info.packet_count, 269)
        self.assertEqual(packet.sender_info.octet_count, 13557)
        self.assertEqual(len(packet.reports), 1)
        self.assertEqual(packet.reports[0].ssrc, 2398654957)
        self.assertEqual(packet.reports[0].fraction_lost, 0)
        self.assertEqual(packet.reports[0].packets_lost, 0)
        self.assertEqual(packet.reports[0].highest_sequence, 246)
        self.assertEqual(packet.reports[0].jitter, 127)
        self.assertEqual(packet.reports[0].lsr, 0)
        self.assertEqual(packet.reports[0].dlsr, 0)
        self.assertEqual(bytes(packet), data[0:52])

    def test_compound(self):
        data = load('rtcp_sr.bin') + load('rtcp_sdes.bin')

        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 2)
        self.assertTrue(isinstance(packets[0], RtcpSrPacket))
        self.assertTrue(isinstance(packets[1], RtcpSdesPacket))

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
    def test_dtmf(self):
        data = load('rtp_dtmf.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 1)
        self.assertEqual(packet.payload_type, 101)
        self.assertEqual(packet.sequence_number, 24152)
        self.assertEqual(packet.timestamp, 4021352124)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extension_profile, 0)
        self.assertEqual(packet.extension_value, None)
        self.assertEqual(len(packet.payload), 4)
        self.assertEqual(bytes(packet), data)

    def test_no_ssrc(self):
        data = load('rtp.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 0)
        self.assertEqual(packet.sequence_number, 15743)
        self.assertEqual(packet.timestamp, 3937035252)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extension_profile, 0)
        self.assertEqual(packet.extension_value, None)
        self.assertEqual(len(packet.payload), 160)
        self.assertEqual(bytes(packet), data)

        self.assertEqual(repr(packet),
                         'RtpPacket(seq=15743, ts=3937035252, marker=0, payload=0, 160 bytes)')

    def test_padding_only(self):
        data = load('rtp_only_padding.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 120)
        self.assertEqual(packet.sequence_number, 27759)
        self.assertEqual(packet.timestamp, 4044047131)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extension_profile, 0)
        self.assertEqual(packet.extension_value, None)
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
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 0)
        self.assertEqual(packet.sequence_number, 16082)
        self.assertEqual(packet.timestamp, 144)
        self.assertEqual(packet.csrc, [2882400001, 3735928559])
        self.assertEqual(packet.extension_profile, 0)
        self.assertEqual(packet.extension_value, None)
        self.assertEqual(len(packet.payload), 160)
        self.assertEqual(bytes(packet), data)

    def test_with_sdes_mid(self):
        data = load('rtp_with_sdes_mid.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 1)
        self.assertEqual(packet.payload_type, 111)
        self.assertEqual(packet.sequence_number, 14156)
        self.assertEqual(packet.timestamp, 1327210925)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extension_profile, 0xBEDE)
        self.assertEqual(packet.extension_value,  b'\x900\x00\x00')
        self.assertEqual(len(packet.payload), 54)
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


class RtpUtilTest(TestCase):
    def test_clamp_packets_lost(self):
        self.assertEqual(clamp_packets_lost(-8388609), -8388608)
        self.assertEqual(clamp_packets_lost(-8388608), -8388608)
        self.assertEqual(clamp_packets_lost(0), 0)
        self.assertEqual(clamp_packets_lost(8388607), 8388607)
        self.assertEqual(clamp_packets_lost(8388608), 8388607)

    def test_datetime_from_ntp(self):
        dt = datetime.datetime(2018, 6, 28, 9, 3, 5, 423998, tzinfo=datetime.timezone.utc)
        self.assertEqual(datetime_from_ntp(16059593044731306503), dt)

    def test_datetime_to_ntp(self):
        dt = datetime.datetime(2018, 6, 28, 9, 3, 5, 423998, tzinfo=datetime.timezone.utc)
        self.assertEqual(datetime_to_ntp(dt), 16059593044731306503)

    def test_pack_packets_lost(self):
        self.assertEqual(pack_packets_lost(-8388608), b'\x80\x00\x00')
        self.assertEqual(pack_packets_lost(-1), b'\xff\xff\xff')
        self.assertEqual(pack_packets_lost(0), b'\x00\x00\x00')
        self.assertEqual(pack_packets_lost(1), b'\x00\x00\x01')
        self.assertEqual(pack_packets_lost(8388607), b'\x7f\xff\xff')

    def test_seq_gt(self):
        self.assertFalse(seq_gt(0, 1))
        self.assertFalse(seq_gt(1, 1))
        self.assertTrue(seq_gt(2, 1))
        self.assertTrue(seq_gt(32768, 1))
        self.assertFalse(seq_gt(32769, 1))
        self.assertFalse(seq_gt(65535, 1))

    def test_seq_plus_one(self):
        self.assertEqual(seq_plus_one(0), 1)
        self.assertEqual(seq_plus_one(1), 2)
        self.assertEqual(seq_plus_one(65535), 0)

    def test_unpack_packets_lost(self):
        self.assertEqual(unpack_packets_lost(b'\x80\x00\x00'), -8388608)
        self.assertEqual(unpack_packets_lost(b'\xff\xff\xff'), -1)
        self.assertEqual(unpack_packets_lost(b'\x00\x00\x00'), 0)
        self.assertEqual(unpack_packets_lost(b'\x00\x00\x01'), 1)
        self.assertEqual(unpack_packets_lost(b'\x7f\xff\xff'), 8388607)

    def test_get_header_extensions(self):
        packet = RtpPacket()
        packet.extension_profile = 0xBEDE

        packet.extension_value = b'\x900\x00\x00'
        self.assertEqual(get_header_extensions(packet), {
            9: b'0',
        })

        packet.extension_value = b'\x10\xc18sdparta_0'
        self.assertEqual(get_header_extensions(packet), {
            1: b'\xc1',
            3: b'sdparta_0',
        })

    def test_set_header_extensions(self):
        packet = RtpPacket()

        set_header_extensions(packet, {9: b'0'})
        self.assertEqual(packet.extension_profile, 0xBEDE)
        self.assertEqual(packet.extension_value, b'\x900\x00\x00')

        set_header_extensions(packet, {
            1: b'\xc1',
            3: b'sdparta_0',
        })
        self.assertEqual(packet.extension_profile, 0xBEDE)
        self.assertEqual(packet.extension_value, b'\x10\xc18sdparta_0')
