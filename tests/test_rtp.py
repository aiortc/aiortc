from unittest import TestCase

from aiortc import rtp
from aiortc.rtcrtpparameters import (RTCRtpHeaderExtensionParameters,
                                     RTCRtpParameters)
from aiortc.rtp import (RtcpByePacket, RtcpPacket, RtcpPsfbPacket,
                        RtcpRrPacket, RtcpRtpfbPacket, RtcpSdesPacket,
                        RtcpSrPacket, RtpPacket, clamp_packets_lost,
                        pack_header_extensions, pack_packets_lost,
                        pack_remb_fci, unpack_header_extensions,
                        unpack_packets_lost, unpack_remb_fci)

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

    def test_psfb_pli(self):
        data = load('rtcp_psfb_pli.bin')
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = packets[0]
        self.assertTrue(isinstance(packet, RtcpPsfbPacket))
        self.assertEqual(packet.fmt, 1)
        self.assertEqual(packet.ssrc, 1414554213)
        self.assertEqual(packet.media_ssrc, 587284409)
        self.assertEqual(packet.fci, b'')
        self.assertEqual(bytes(packet), data)

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
        self.assertEqual(bytes(packet), data)

    def test_rtpfb(self):
        data = load('rtcp_rtpfb.bin')
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = packets[0]
        self.assertTrue(isinstance(packet, RtcpRtpfbPacket))
        self.assertEqual(packet.fmt, 1)
        self.assertEqual(packet.ssrc, 2336520123)
        self.assertEqual(packet.media_ssrc, 4145934052)
        self.assertEqual(packet.lost,
                         [12, 32, 39, 54, 76, 110, 123, 142, 183, 187, 223, 236, 271, 292])
        self.assertEqual(bytes(packet), data)

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
        self.assertEqual(packet.extensions, rtp.HeaderExtensions())
        self.assertEqual(len(packet.payload), 4)
        self.assertEqual(packet.serialize(), data)

    def test_no_ssrc(self):
        data = load('rtp.bin')
        packet = RtpPacket.parse(data)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 0)
        self.assertEqual(packet.sequence_number, 15743)
        self.assertEqual(packet.timestamp, 3937035252)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extensions, rtp.HeaderExtensions())
        self.assertEqual(len(packet.payload), 160)
        self.assertEqual(packet.serialize(), data)

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
        self.assertEqual(packet.extensions, rtp.HeaderExtensions())
        self.assertEqual(len(packet.payload), 0)
        self.assertEqual(packet.padding_size, 224)

        serialized = packet.serialize()
        self.assertEqual(len(serialized), len(data))
        self.assertEqual(serialized[0:12], data[0:12])
        self.assertEqual(serialized[-1], data[-1])

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
        self.assertEqual(packet.extensions, rtp.HeaderExtensions())
        self.assertEqual(len(packet.payload), 160)
        self.assertEqual(packet.serialize(), data)

    def test_with_sdes_mid(self):
        extensions_map = rtp.HeaderExtensionsMap()
        extensions_map.configure(RTCRtpParameters(
            headerExtensions=[
                RTCRtpHeaderExtensionParameters(
                    id=9,
                    uri='urn:ietf:params:rtp-hdrext:sdes:mid'),
            ]))

        data = load('rtp_with_sdes_mid.bin')
        packet = RtpPacket.parse(data, extensions_map)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 1)
        self.assertEqual(packet.payload_type, 111)
        self.assertEqual(packet.sequence_number, 14156)
        self.assertEqual(packet.timestamp, 1327210925)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extensions, rtp.HeaderExtensions(mid='0'))
        self.assertEqual(len(packet.payload), 54)
        self.assertEqual(packet.serialize(extensions_map), data)

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

    def test_pack_packets_lost(self):
        self.assertEqual(pack_packets_lost(-8388608), b'\x80\x00\x00')
        self.assertEqual(pack_packets_lost(-1), b'\xff\xff\xff')
        self.assertEqual(pack_packets_lost(0), b'\x00\x00\x00')
        self.assertEqual(pack_packets_lost(1), b'\x00\x00\x01')
        self.assertEqual(pack_packets_lost(8388607), b'\x7f\xff\xff')

    def test_pack_remb_fci(self):
        # exponent = 0, mantissa = 0
        data = pack_remb_fci(0, [2529072847])
        self.assertEqual(data, b'REMB\x01\x00\x00\x00\x96\xbe\x96\xcf')

        # exponent = 0, mantissa = 0x3ffff
        data = pack_remb_fci(0x3ffff, [2529072847])
        self.assertEqual(data, b'REMB\x01\x03\xff\xff\x96\xbe\x96\xcf')

        # exponent = 1, mantissa = 0
        data = pack_remb_fci(0x40000, [2529072847])
        self.assertEqual(data, b'REMB\x01\x06\x00\x00\x96\xbe\x96\xcf')

        data = pack_remb_fci(4160000, [2529072847])
        self.assertEqual(data, b'REMB\x01\x13\xf7\xa0\x96\xbe\x96\xcf')

        # exponent = 63, mantissa = 0x3ffff
        data = pack_remb_fci(0x3ffff << 63, [2529072847])
        self.assertEqual(data, b'REMB\x01\xff\xff\xff\x96\xbe\x96\xcf')

    def test_unpack_packets_lost(self):
        self.assertEqual(unpack_packets_lost(b'\x80\x00\x00'), -8388608)
        self.assertEqual(unpack_packets_lost(b'\xff\xff\xff'), -1)
        self.assertEqual(unpack_packets_lost(b'\x00\x00\x00'), 0)
        self.assertEqual(unpack_packets_lost(b'\x00\x00\x01'), 1)
        self.assertEqual(unpack_packets_lost(b'\x7f\xff\xff'), 8388607)

    def test_unpack_remb_fci(self):
        # junk
        with self.assertRaises(ValueError):
            unpack_remb_fci(b'JUNK')

        # exponent = 0, mantissa = 0
        bitrate, ssrcs = unpack_remb_fci(b'REMB\x01\x00\x00\x00\x96\xbe\x96\xcf')
        self.assertEqual(bitrate, 0)
        self.assertEqual(ssrcs, [2529072847])

        # exponent = 0, mantissa = 0x3ffff
        bitrate, ssrcs = unpack_remb_fci(b'REMB\x01\x03\xff\xff\x96\xbe\x96\xcf')
        self.assertEqual(bitrate, 0x3ffff)
        self.assertEqual(ssrcs, [2529072847])

        # exponent = 1, mantissa = 0
        bitrate, ssrcs = unpack_remb_fci(b'REMB\x01\x06\x00\x00\x96\xbe\x96\xcf')
        self.assertEqual(bitrate, 0x40000)
        self.assertEqual(ssrcs, [2529072847])

        # 4160000 bps
        bitrate, ssrcs = unpack_remb_fci(b'REMB\x01\x13\xf7\xa0\x96\xbe\x96\xcf')
        self.assertEqual(bitrate, 4160000)
        self.assertEqual(ssrcs, [2529072847])

        # exponent = 63, mantissa = 0x3ffff
        bitrate, ssrcs = unpack_remb_fci(b'REMB\x01\xff\xff\xff\x96\xbe\x96\xcf')
        self.assertEqual(bitrate, 0x3ffff << 63)
        self.assertEqual(ssrcs, [2529072847])

    def test_unpack_header_extensions(self):
        # none
        self.assertEqual(unpack_header_extensions(0, None), [])

        # one-byte, single value
        self.assertEqual(unpack_header_extensions(0xBEDE, b'\x900\x00\x00'), [
            (9, b'0'),
        ])

        # one-byte, two values
        self.assertEqual(unpack_header_extensions(0xBEDE, b'\x10\xc18sdparta_0'), [
            (1, b'\xc1'),
            (3, b'sdparta_0'),
        ])

        # two-byte, single value
        self.assertEqual(unpack_header_extensions(0x1000, b'\xff\x010\x00'), [
            (255, b'0'),
        ])

    def test_pack_header_extensions(self):
        # none
        self.assertEqual(pack_header_extensions([]), (0, b''))

        # one-byte, single value
        self.assertEqual(pack_header_extensions([
            (9, b'0'),
        ]), (0xBEDE, b'\x900\x00\x00'))

        # one-byte, two values
        self.assertEqual(pack_header_extensions([
            (1, b'\xc1'),
            (3, b'sdparta_0'),
        ]), (0xBEDE, b'\x10\xc18sdparta_0'))

        # two-byte, single value
        self.assertEqual(pack_header_extensions([
            (255, b'0'),
        ]), (0x1000, b'\xff\x010\x00'))

    def test_map_header_extensions(self):
        data = bytearray([
            0x90, 0x64, 0x00, 0x58,
            0x65, 0x43, 0x12, 0x78,
            0x12, 0x34, 0x56, 0x78,  # SSRC
            0xbe, 0xde, 0x00, 0x08,  # Extension of size 8x32bit words.
            0x40, 0xda,              # AudioLevel.
            0x22, 0x01, 0x56, 0xce,  # TransmissionOffset.
            0x62, 0x12, 0x34, 0x56,  # AbsoluteSendTime.
            0x81, 0xce, 0xab,        # TransportSequenceNumber.
            0xa0, 0x03,              # VideoRotation.
            0xb2, 0x12, 0x48, 0x76,  # PlayoutDelayLimits.
            0xc2, 0x72, 0x74, 0x78,  # RtpStreamId
            0xd5, 0x73, 0x74, 0x72, 0x65, 0x61, 0x6d,  # RepairedRtpStreamId
            0x00, 0x00,              # Padding to 32bit boundary.
        ])
        extensions_map = rtp.HeaderExtensionsMap()
        extensions_map.configure(RTCRtpParameters(
            headerExtensions=[
                RTCRtpHeaderExtensionParameters(
                    id=2,
                    uri='urn:ietf:params:rtp-hdrext:toffset'),
                RTCRtpHeaderExtensionParameters(
                    id=4,
                    uri='urn:ietf:params:rtp-hdrext:ssrc-audio-level'),
                RTCRtpHeaderExtensionParameters(
                    id=6,
                    uri='http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time'),
                RTCRtpHeaderExtensionParameters(
                    id=8,
                    uri='http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01'),  # noqa
                RTCRtpHeaderExtensionParameters(
                    id=12,
                    uri='urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id'),
                RTCRtpHeaderExtensionParameters(
                    id=13,
                    uri='urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-sream-id'),
            ]))

        packet = RtpPacket.parse(data, extensions_map)

        # check mapped values
        self.assertEqual(packet.extensions.abs_send_time, 0x123456)
        self.assertEqual(packet.extensions.audio_level, (True, 90))
        self.assertEqual(packet.extensions.mid, None)
        self.assertEqual(packet.extensions.repaired_rtp_stream_id, 'stream')
        self.assertEqual(packet.extensions.rtp_stream_id, 'rtx')
        self.assertEqual(packet.extensions.transmission_offset, 0x156ce)
        self.assertEqual(packet.extensions.transport_sequence_number, 0xceab)

        # TODO: check
        packet.serialize(extensions_map)
