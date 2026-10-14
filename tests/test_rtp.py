import fractions
import math
import sys
from collections.abc import Callable

from aiortc import rtp
from aiortc.rtcrtpparameters import RTCRtpHeaderExtensionParameters, RTCRtpParameters
from aiortc.rtp import (
    RtcpByePacket,
    RtcpPacket,
    RtcpPsfbPacket,
    RtcpRrPacket,
    RtcpRtpfbPacket,
    RtcpSdesPacket,
    RtcpSrPacket,
    RtcpTwccPacket,
    RtpPacket,
    clamp_packets_lost,
    pack_header_extensions,
    pack_packets_lost,
    pack_remb_fci,
    unpack_header_extensions,
    unpack_packets_lost,
    unpack_remb_fci,
    unwrap_rtx,
    wrap_rtx,
)
from av import AudioFrame

from .utils import TestCase, load


def create_audio_frame(
    sample_func: Callable[[int], float],
    samples: int,
    pts: int,
    layout: str = "mono",
    sample_rate: int = 48000,
) -> AudioFrame:
    frame = AudioFrame(format="s16", layout=layout, samples=samples)
    for p in frame.planes:
        buf = b""
        for i in range(samples):
            sample = int(sample_func(i) * 32767)
            buf += int.to_bytes(sample, 2, sys.byteorder, signed=True)
        p.update(buf)
    frame.pts = pts
    frame.sample_rate = sample_rate
    frame.time_base = fractions.Fraction(1, sample_rate)
    return frame


class RtcpPacketTest(TestCase):
    def test_bye(self) -> None:
        data = load("rtcp_bye.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpByePacket)
        self.assertEqual(packet.sources, [2924645187])
        self.assertEqual(bytes(packet), data)

        self.assertEqual(repr(packet), "RtcpByePacket(sources=[2924645187])")

    def test_bye_invalid(self) -> None:
        data = load("rtcp_bye_invalid.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP bye length is invalid")

    def test_bye_no_sources(self) -> None:
        data = load("rtcp_bye_no_sources.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpByePacket)
        self.assertEqual(packet.sources, [])
        self.assertEqual(bytes(packet), data)

        self.assertEqual(repr(packet), "RtcpByePacket(sources=[])")

    def test_bye_only_padding(self) -> None:
        data = load("rtcp_bye_padding.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpByePacket)
        self.assertEqual(packet.sources, [])
        self.assertEqual(bytes(packet), b"\x80\xcb\x00\x00")

        self.assertEqual(repr(packet), "RtcpByePacket(sources=[])")

    def test_bye_only_padding_zero(self) -> None:
        data = load("rtcp_bye_padding.bin")[0:4] + b"\x00\x00\x00\x00"

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP packet padding length is invalid")

    def test_psfb_invalid(self) -> None:
        data = load("rtcp_psfb_invalid.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(
            str(cm.exception), "RTCP payload-specific feedback length is invalid"
        )

    def test_psfb_pli(self) -> None:
        data = load("rtcp_psfb_pli.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpPsfbPacket)
        self.assertEqual(packet.fmt, 1)
        self.assertEqual(packet.ssrc, 1414554213)
        self.assertEqual(packet.media_ssrc, 587284409)
        self.assertEqual(packet.fci, b"")
        self.assertEqual(bytes(packet), data)

    def test_rr(self) -> None:
        data = load("rtcp_rr.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpRrPacket)
        self.assertEqual(packet.ssrc, 817267719)
        self.assertEqual(packet.reports[0].ssrc, 1200895919)
        self.assertEqual(packet.reports[0].fraction_lost, 0)
        self.assertEqual(packet.reports[0].packets_lost, 0)
        self.assertEqual(packet.reports[0].highest_sequence, 630)
        self.assertEqual(packet.reports[0].jitter, 1906)
        self.assertEqual(packet.reports[0].lsr, 0)
        self.assertEqual(packet.reports[0].dlsr, 0)
        self.assertEqual(bytes(packet), data)

    def test_rr_invalid(self) -> None:
        data = load("rtcp_rr_invalid.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP receiver report length is invalid")

    def test_rr_truncated(self) -> None:
        data = load("rtcp_rr.bin")

        for length in range(1, 4):
            with self.assertRaises(ValueError) as cm:
                RtcpPacket.parse(data[0:length])
            self.assertEqual(
                str(cm.exception), "RTCP packet length is less than 4 bytes"
            )

        for length in range(4, 32):
            with self.assertRaises(ValueError) as cm:
                RtcpPacket.parse(data[0:length])
            self.assertEqual(str(cm.exception), "RTCP packet is truncated")

    def test_sdes(self) -> None:
        data = load("rtcp_sdes.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpSdesPacket)
        self.assertEqual(packet.chunks[0].ssrc, 1831097322)
        self.assertEqual(
            packet.chunks[0].items, [(1, b"{63f459ea-41fe-4474-9d33-9707c9ee79d1}")]
        )
        self.assertEqual(bytes(packet), data)

    def test_sdes_item_truncated(self) -> None:
        data = load("rtcp_sdes_item_truncated.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP SDES item is truncated")

    def test_sdes_source_truncated(self) -> None:
        data = load("rtcp_sdes_source_truncated.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP SDES source is truncated")

    def test_sr(self) -> None:
        data = load("rtcp_sr.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpSrPacket)
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

    def test_sr_invalid(self) -> None:
        data = load("rtcp_sr_invalid.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP sender report length is invalid")

    def test_rtpfb(self) -> None:
        data = load("rtcp_rtpfb.bin")
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)

        packet = self.ensureIsInstance(packets[0], RtcpRtpfbPacket)
        self.assertEqual(packet.fmt, 1)
        self.assertEqual(packet.ssrc, 2336520123)
        self.assertEqual(packet.media_ssrc, 4145934052)
        self.assertEqual(
            packet.lost,
            [12, 32, 39, 54, 76, 110, 123, 142, 183, 187, 223, 236, 271, 292],
        )
        self.assertEqual(bytes(packet), data)

    def test_rtpfb_invalid(self) -> None:
        data = load("rtcp_rtpfb_invalid.bin")

        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP RTP feedback length is invalid")

    def test_compound(self) -> None:
        data = load("rtcp_sr.bin") + load("rtcp_sdes.bin")

        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 2)
        self.assertIsInstance(packets[0], RtcpSrPacket)
        self.assertIsInstance(packets[1], RtcpSdesPacket)

    def test_bad_version(self) -> None:
        data = b"\xc0" + load("rtcp_rr.bin")[1:]
        with self.assertRaises(ValueError) as cm:
            RtcpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTCP packet has invalid version")


class RtpPacketTest(TestCase):
    def test_dtmf(self) -> None:
        data = load("rtp_dtmf.bin")
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

    def test_no_ssrc(self) -> None:
        data = load("rtp.bin")
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

        self.assertEqual(
            repr(packet),
            "RtpPacket(seq=15743, ts=3937035252, marker=0, payload=0, 160 bytes)",
        )

    def test_padding_only(self) -> None:
        data = load("rtp_only_padding.bin")
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

    def test_padding_only_with_header_extensions(self) -> None:
        extensions_map = rtp.HeaderExtensionsMap()
        extensions_map.configure(
            RTCRtpParameters(
                headerExtensions=[
                    RTCRtpHeaderExtensionParameters(
                        id=2,
                        uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
                    )
                ]
            )
        )

        data = load("rtp_only_padding_with_header_extensions.bin")
        packet = RtpPacket.parse(data, extensions_map)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 0)
        self.assertEqual(packet.payload_type, 98)
        self.assertEqual(packet.sequence_number, 22138)
        self.assertEqual(packet.timestamp, 3171065731)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(
            packet.extensions, rtp.HeaderExtensions(abs_send_time=15846540)
        )
        self.assertEqual(len(packet.payload), 0)
        self.assertEqual(packet.padding_size, 224)

        serialized = packet.serialize(extensions_map)
        self.assertEqual(len(serialized), len(data))
        self.assertEqual(serialized[0:20], data[0:20])
        self.assertEqual(serialized[-1], data[-1])

    def test_padding_too_long(self) -> None:
        data = load("rtp_only_padding.bin")[0:12] + b"\x02"
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTP packet padding length is invalid")

    def test_padding_zero(self) -> None:
        data = load("rtp_only_padding.bin")[0:12] + b"\x00"
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTP packet padding length is invalid")

    def test_with_csrc(self) -> None:
        data = load("rtp_with_csrc.bin")
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

    def test_with_csrc_truncated(self) -> None:
        data = load("rtp_with_csrc.bin")
        for length in range(12, 20):
            with self.assertRaises(ValueError) as cm:
                RtpPacket.parse(data[0:length])
            self.assertEqual(str(cm.exception), "RTP packet has truncated CSRC")

    def test_with_sdes_mid(self) -> None:
        extensions_map = rtp.HeaderExtensionsMap()
        extensions_map.configure(
            RTCRtpParameters(
                headerExtensions=[
                    RTCRtpHeaderExtensionParameters(
                        id=9, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                    )
                ]
            )
        )

        data = load("rtp_with_sdes_mid.bin")
        packet = RtpPacket.parse(data, extensions_map)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.marker, 1)
        self.assertEqual(packet.payload_type, 111)
        self.assertEqual(packet.sequence_number, 14156)
        self.assertEqual(packet.timestamp, 1327210925)
        self.assertEqual(packet.csrc, [])
        self.assertEqual(packet.extensions, rtp.HeaderExtensions(mid="0"))
        self.assertEqual(len(packet.payload), 54)
        self.assertEqual(packet.serialize(extensions_map), data)

    def test_with_sdes_mid_truncated(self) -> None:
        data = load("rtp_with_sdes_mid.bin")

        for length in range(12, 16):
            with self.assertRaises(ValueError) as cm:
                RtpPacket.parse(data[0:length])
            self.assertEqual(
                str(cm.exception), "RTP packet has truncated extension profile / length"
            )

        for length in range(16, 20):
            with self.assertRaises(ValueError) as cm:
                RtpPacket.parse(data[0:length])
            self.assertEqual(
                str(cm.exception), "RTP packet has truncated extension value"
            )

    def test_truncated(self) -> None:
        data = load("rtp.bin")[0:11]
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTP packet length is less than 12 bytes")

    def test_bad_version(self) -> None:
        data = b"\xc0" + load("rtp.bin")[1:]
        with self.assertRaises(ValueError) as cm:
            RtpPacket.parse(data)
        self.assertEqual(str(cm.exception), "RTP packet has invalid version")


class RtpUtilTest(TestCase):
    def test_clamp_packets_lost(self) -> None:
        self.assertEqual(clamp_packets_lost(-8388609), -8388608)
        self.assertEqual(clamp_packets_lost(-8388608), -8388608)
        self.assertEqual(clamp_packets_lost(0), 0)
        self.assertEqual(clamp_packets_lost(8388607), 8388607)
        self.assertEqual(clamp_packets_lost(8388608), 8388607)

    def test_pack_packets_lost(self) -> None:
        self.assertEqual(pack_packets_lost(-8388608), b"\x80\x00\x00")
        self.assertEqual(pack_packets_lost(-1), b"\xff\xff\xff")
        self.assertEqual(pack_packets_lost(0), b"\x00\x00\x00")
        self.assertEqual(pack_packets_lost(1), b"\x00\x00\x01")
        self.assertEqual(pack_packets_lost(8388607), b"\x7f\xff\xff")

    def test_pack_remb_fci(self) -> None:
        # exponent = 0, mantissa = 0
        data = pack_remb_fci(0, [2529072847])
        self.assertEqual(data, b"REMB\x01\x00\x00\x00\x96\xbe\x96\xcf")

        # exponent = 0, mantissa = 0x3ffff
        data = pack_remb_fci(0x3FFFF, [2529072847])
        self.assertEqual(data, b"REMB\x01\x03\xff\xff\x96\xbe\x96\xcf")

        # exponent = 1, mantissa = 0
        data = pack_remb_fci(0x40000, [2529072847])
        self.assertEqual(data, b"REMB\x01\x06\x00\x00\x96\xbe\x96\xcf")

        data = pack_remb_fci(4160000, [2529072847])
        self.assertEqual(data, b"REMB\x01\x13\xf7\xa0\x96\xbe\x96\xcf")

        # exponent = 63, mantissa = 0x3ffff
        data = pack_remb_fci(0x3FFFF << 63, [2529072847])
        self.assertEqual(data, b"REMB\x01\xff\xff\xff\x96\xbe\x96\xcf")

    def test_unpack_packets_lost(self) -> None:
        self.assertEqual(unpack_packets_lost(b"\x80\x00\x00"), -8388608)
        self.assertEqual(unpack_packets_lost(b"\xff\xff\xff"), -1)
        self.assertEqual(unpack_packets_lost(b"\x00\x00\x00"), 0)
        self.assertEqual(unpack_packets_lost(b"\x00\x00\x01"), 1)
        self.assertEqual(unpack_packets_lost(b"\x7f\xff\xff"), 8388607)

    def test_unpack_remb_fci(self) -> None:
        # junk
        with self.assertRaises(ValueError):
            unpack_remb_fci(b"JUNK")

        # exponent = 0, mantissa = 0
        bitrate, ssrcs = unpack_remb_fci(b"REMB\x01\x00\x00\x00\x96\xbe\x96\xcf")
        self.assertEqual(bitrate, 0)
        self.assertEqual(ssrcs, [2529072847])

        # exponent = 0, mantissa = 0x3ffff
        bitrate, ssrcs = unpack_remb_fci(b"REMB\x01\x03\xff\xff\x96\xbe\x96\xcf")
        self.assertEqual(bitrate, 0x3FFFF)
        self.assertEqual(ssrcs, [2529072847])

        # exponent = 1, mantissa = 0
        bitrate, ssrcs = unpack_remb_fci(b"REMB\x01\x06\x00\x00\x96\xbe\x96\xcf")
        self.assertEqual(bitrate, 0x40000)
        self.assertEqual(ssrcs, [2529072847])

        # 4160000 bps
        bitrate, ssrcs = unpack_remb_fci(b"REMB\x01\x13\xf7\xa0\x96\xbe\x96\xcf")
        self.assertEqual(bitrate, 4160000)
        self.assertEqual(ssrcs, [2529072847])

        # exponent = 63, mantissa = 0x3ffff
        bitrate, ssrcs = unpack_remb_fci(b"REMB\x01\xff\xff\xff\x96\xbe\x96\xcf")
        self.assertEqual(bitrate, 0x3FFFF << 63)
        self.assertEqual(ssrcs, [2529072847])

    def test_unpack_header_extensions(self) -> None:
        # none
        self.assertEqual(unpack_header_extensions(0, None), [])

        # one-byte, value
        self.assertEqual(unpack_header_extensions(0xBEDE, b"\x900"), [(9, b"0")])

        # one-byte, value, padding, value
        self.assertEqual(
            unpack_header_extensions(0xBEDE, b"\x900\x00\x00\x301"),
            [(9, b"0"), (3, b"1")],
        )

        # one-byte, value, value
        self.assertEqual(
            unpack_header_extensions(0xBEDE, b"\x10\xc18sdparta_0"),
            [(1, b"\xc1"), (3, b"sdparta_0")],
        )

        # two-byte, value
        self.assertEqual(unpack_header_extensions(0x1000, b"\xff\x010"), [(255, b"0")])

        # two-byte, value (1 byte), padding, value (2 bytes)
        self.assertEqual(
            unpack_header_extensions(0x1000, b"\xff\x010\x00\xf0\x0212"),
            [(255, b"0"), (240, b"12")],
        )

    def test_unpack_header_extensions_bad(self) -> None:
        # one-byte, value (truncated)
        with self.assertRaises(ValueError) as cm:
            unpack_header_extensions(0xBEDE, b"\x90")
        self.assertEqual(
            str(cm.exception), "RTP one-byte header extension value is truncated"
        )

        # two-byte (truncated)
        with self.assertRaises(ValueError) as cm:
            unpack_header_extensions(0x1000, b"\xff")
        self.assertEqual(
            str(cm.exception), "RTP two-byte header extension is truncated"
        )

        # two-byte, value (truncated)
        with self.assertRaises(ValueError) as cm:
            unpack_header_extensions(0x1000, b"\xff\x020")
        self.assertEqual(
            str(cm.exception), "RTP two-byte header extension value is truncated"
        )

    def test_pack_header_extensions(self) -> None:
        # none
        self.assertEqual(pack_header_extensions([]), (0, b""))

        # one-byte, single value
        self.assertEqual(
            pack_header_extensions([(9, b"0")]), (0xBEDE, b"\x900\x00\x00")
        )

        # one-byte, two values
        self.assertEqual(
            pack_header_extensions([(1, b"\xc1"), (3, b"sdparta_0")]),
            (0xBEDE, b"\x10\xc18sdparta_0"),
        )

        # two-byte, single value
        self.assertEqual(
            pack_header_extensions([(255, b"0")]), (0x1000, b"\xff\x010\x00")
        )

    def test_map_header_extensions(self) -> None:
        data = bytes(
            [
                0x90,
                0x64,
                0x00,
                0x58,
                0x65,
                0x43,
                0x12,
                0x78,
                0x12,
                0x34,
                0x56,
                0x78,  # SSRC
                0xBE,
                0xDE,
                0x00,
                0x08,  # Extension of size 8x32bit words.
                0x40,
                0xDA,  # AudioLevel.
                0x22,
                0x01,
                0x56,
                0xCE,  # TransmissionOffset.
                0x62,
                0x12,
                0x34,
                0x56,  # AbsoluteSendTime.
                0x81,
                0xCE,
                0xAB,  # TransportSequenceNumber.
                0xA0,
                0x03,  # VideoRotation.
                0xB2,
                0x12,
                0x48,
                0x76,  # PlayoutDelayLimits.
                0xC2,
                0x72,
                0x74,
                0x78,  # RtpStreamId
                0xD5,
                0x73,
                0x74,
                0x72,
                0x65,
                0x61,
                0x6D,  # RepairedRtpStreamId
                0x00,
                0x00,  # Padding to 32bit boundary.
            ]
        )
        extensions_map = rtp.HeaderExtensionsMap()
        extensions_map.configure(
            RTCRtpParameters(
                headerExtensions=[
                    RTCRtpHeaderExtensionParameters(
                        id=2, uri="urn:ietf:params:rtp-hdrext:toffset"
                    ),
                    RTCRtpHeaderExtensionParameters(
                        id=4, uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                    ),
                    RTCRtpHeaderExtensionParameters(
                        id=6,
                        uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
                    ),
                    RTCRtpHeaderExtensionParameters(
                        id=8,
                        uri="http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
                    ),
                    RTCRtpHeaderExtensionParameters(
                        id=12, uri="urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id"
                    ),
                    RTCRtpHeaderExtensionParameters(
                        id=13,
                        uri="urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id",
                    ),
                ]
            )
        )

        packet = RtpPacket.parse(data, extensions_map)

        # check mapped values
        self.assertEqual(packet.extensions.abs_send_time, 0x123456)
        self.assertEqual(packet.extensions.audio_level, (True, 90))
        self.assertEqual(packet.extensions.mid, None)
        self.assertEqual(packet.extensions.repaired_rtp_stream_id, "stream")
        self.assertEqual(packet.extensions.rtp_stream_id, "rtx")
        self.assertEqual(packet.extensions.transmission_offset, 0x156CE)
        self.assertEqual(packet.extensions.transport_sequence_number, 0xCEAB)

        # TODO: check
        packet.serialize(extensions_map)

    def test_rtx(self) -> None:
        extensions_map = rtp.HeaderExtensionsMap()
        extensions_map.configure(
            RTCRtpParameters(
                headerExtensions=[
                    RTCRtpHeaderExtensionParameters(
                        id=9, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                    )
                ]
            )
        )

        data = load("rtp_with_sdes_mid.bin")
        packet = RtpPacket.parse(data, extensions_map)

        # wrap / unwrap RTX
        rtx = wrap_rtx(packet, payload_type=112, sequence_number=12345, ssrc=1234)
        recovered = unwrap_rtx(rtx, payload_type=111, ssrc=4084547440)

        # check roundtrip
        self.assertEqual(recovered.version, packet.version)
        self.assertEqual(recovered.marker, packet.marker)
        self.assertEqual(recovered.payload_type, packet.payload_type)
        self.assertEqual(recovered.sequence_number, packet.sequence_number)
        self.assertEqual(recovered.timestamp, packet.timestamp)
        self.assertEqual(recovered.ssrc, packet.ssrc)
        self.assertEqual(recovered.csrc, packet.csrc)
        self.assertEqual(recovered.extensions, packet.extensions)
        self.assertEqual(recovered.payload, packet.payload)

    def test_compute_audio_level_dbov(self) -> None:
        num_samples = 960  # 20ms @ 48kHz
        # test a frame of all zeroes (-127 dBov, the minimum value)
        silent_frame = create_audio_frame(lambda n: 0.0, num_samples, 0)
        self.assertEqual(rtp.compute_audio_level_dbov(silent_frame), -127)
        # test a 50Hz square wave (0 dBov, the maximum value)
        square_frame = create_audio_frame(
            lambda n: 1.0 if n < num_samples / 2 else -1.0, num_samples, 0
        )
        self.assertEqual(rtp.compute_audio_level_dbov(square_frame), 0)
        # test a 50Hz sine wave (-3 dBov, the maximum value for a sine wave)
        sine_frame = create_audio_frame(
            lambda n: math.sin(2 * math.pi * n / num_samples), num_samples, 0
        )
        self.assertEqual(rtp.compute_audio_level_dbov(sine_frame), -3)


class RtcpTwccPacketTest(TestCase):
    def test_parse_simple(self) -> None:
        """All packets received with small deltas."""
        packet = RtcpTwccPacket(
            ssrc=1,
            media_ssrc=2,
            base_sequence_number=100,
            packet_status_count=3,
            reference_time=1000,
            feedback_packet_count=0,
            packet_results=[
                (100, 250),  # 1 tick
                (101, 750),  # 2 ticks from ref
                (102, 1500),  # 6 ticks from ref
            ],
        )
        data = bytes(packet)
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)
        parsed = self.ensureIsInstance(packets[0], RtcpTwccPacket)
        self.assertEqual(parsed.ssrc, 1)
        self.assertEqual(parsed.media_ssrc, 2)
        self.assertEqual(parsed.base_sequence_number, 100)
        self.assertEqual(parsed.packet_status_count, 3)
        self.assertEqual(parsed.reference_time, 1000)
        self.assertEqual(parsed.feedback_packet_count, 0)
        self.assertEqual(len(parsed.packet_results), 3)
        for seq, delta in parsed.packet_results:
            self.assertIsNotNone(delta)

    def test_parse_with_loss(self) -> None:
        """Some packets lost."""
        packet = RtcpTwccPacket(
            ssrc=1,
            media_ssrc=2,
            base_sequence_number=10,
            packet_status_count=4,
            reference_time=500,
            feedback_packet_count=1,
            packet_results=[
                (10, 250),
                (11, None),  # lost
                (12, 1000),
                (13, None),  # lost
            ],
        )
        data = bytes(packet)
        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)
        parsed = self.ensureIsInstance(packets[0], RtcpTwccPacket)
        self.assertEqual(parsed.packet_status_count, 4)
        self.assertEqual(len(parsed.packet_results), 4)
        self.assertIsNotNone(parsed.packet_results[0][1])
        self.assertIsNone(parsed.packet_results[1][1])
        self.assertIsNotNone(parsed.packet_results[2][1])
        self.assertIsNone(parsed.packet_results[3][1])

    def test_parse_large_delta(self) -> None:
        """Packet with 2-byte signed delta."""
        packet = RtcpTwccPacket(
            ssrc=1,
            media_ssrc=2,
            base_sequence_number=0,
            packet_status_count=2,
            reference_time=100,
            feedback_packet_count=0,
            packet_results=[
                (0, 250),
                (1, 100000),  # large delta
            ],
        )
        data = bytes(packet)
        packets = RtcpPacket.parse(data)
        parsed = self.ensureIsInstance(packets[0], RtcpTwccPacket)
        self.assertEqual(len(parsed.packet_results), 2)
        # First should be small delta
        self.assertIsNotNone(parsed.packet_results[0][1])
        # Second should be large delta
        self.assertIsNotNone(parsed.packet_results[1][1])

    def test_roundtrip(self) -> None:
        """Create, serialize, parse, and verify fields match."""
        original = RtcpTwccPacket(
            ssrc=0x12345678,
            media_ssrc=0xABCDEF01,
            base_sequence_number=5000,
            packet_status_count=5,
            reference_time=2000,
            feedback_packet_count=42,
            packet_results=[
                (5000, 500),
                (5001, 1000),
                (5002, None),
                (5003, 2000),
                (5004, 2500),
            ],
        )
        data = bytes(original)
        packets = RtcpPacket.parse(data)
        parsed = self.ensureIsInstance(packets[0], RtcpTwccPacket)
        self.assertEqual(parsed.ssrc, original.ssrc)
        self.assertEqual(parsed.media_ssrc, original.media_ssrc)
        self.assertEqual(parsed.base_sequence_number, original.base_sequence_number)
        self.assertEqual(parsed.packet_status_count, original.packet_status_count)
        self.assertEqual(parsed.reference_time, original.reference_time)
        self.assertEqual(parsed.feedback_packet_count, original.feedback_packet_count)
        self.assertEqual(len(parsed.packet_results), len(original.packet_results))
        for (seq_o, delta_o), (seq_p, delta_p) in zip(
            original.packet_results, parsed.packet_results
        ):
            self.assertEqual(seq_o, seq_p)
            if delta_o is None:
                self.assertIsNone(delta_p)
            else:
                self.assertIsNotNone(delta_p)

    def test_serialize_run_length(self) -> None:
        """Verify run-length encoding output."""
        from aiortc.rtp import _encode_twcc_chunks

        # All same status
        statuses = [1] * 10
        data = _encode_twcc_chunks(statuses)
        # Should produce one run-length chunk
        self.assertEqual(len(data), 2)
        chunk = int.from_bytes(data[0:2], "big")
        self.assertEqual(chunk & 0x8000, 0)  # run-length
        self.assertEqual((chunk >> 13) & 0x03, 1)  # status=1
        self.assertEqual(chunk & 0x1FFF, 10)  # run=10

    def test_parse_status_vector_1bit(self) -> None:
        """Parse a status vector chunk with 1-bit symbols."""
        from struct import pack

        from aiortc.rtp import _decode_twcc_chunks

        # Status vector: bit15=1, bit14=0 (1-bit), then 14 bits of symbols
        # Symbols: 1,0,1,0,1,0,1,0,1,0,1,0,1,0
        symbols = 0b10101010101010
        chunk = 0x8000 | symbols  # bit15=1, bit14=0
        data = pack("!H", chunk)
        result = _decode_twcc_chunks(data, 0, 14)
        self.assertEqual(len(result), 14)
        for i in range(14):
            expected = 1 if i % 2 == 0 else 0
            self.assertEqual(result[i], expected)

    def test_parse_status_vector_2bit(self) -> None:
        """Parse a status vector chunk with 2-bit symbols."""
        from struct import pack

        from aiortc.rtp import _decode_twcc_chunks

        # Status vector: bit15=1, bit14=1 (2-bit), then 7 x 2-bit symbols
        # Symbols: 0,1,2,0,1,2,0
        symbols = 0b00011000011000
        chunk = 0xC000 | symbols
        data = pack("!H", chunk)
        result = _decode_twcc_chunks(data, 0, 7)
        self.assertEqual(len(result), 7)
        self.assertEqual(result, [0, 1, 2, 0, 1, 2, 0])

    def test_parse_reference_vectors(self) -> None:
        """
        Tests parsing of pre-built TWCC binary payloads.
        """
        # Vector 1: Simple 3 packets all received, small deltas
        # RTCP header: V=2, PT=205, FMT=15, length in words
        # Build a simple TWCC payload manually:
        # ssrc=1, media_ssrc=2, base_seq=0, count=3, ref_time=0, fb_count=0
        # Run-length chunk: status=1 (small delta), run=3
        # Deltas: 1 tick, 1 tick, 1 tick (250us each)
        from struct import pack

        from aiortc.rtp import RTCP_RTPFB, RTCP_RTPFB_TWCC, pack_rtcp_packet

        payload = pack("!LL", 1, 2)  # ssrc, media_ssrc
        payload += pack("!HH", 0, 3)  # base_seq, status_count
        payload += pack("!BBB", 0, 0, 0)  # ref_time 24-bit = 0
        payload += pack("!B", 0)  # fb_pkt_count
        # Run-length chunk: status=1, run=3
        payload += pack("!H", (1 << 13) | 3)
        # Deltas: 1, 1, 1 (each 250us)
        payload += pack("!BBB", 1, 1, 1)
        # Pad to 4-byte boundary (total=21, need 24)
        payload += b"\x00" * ((4 - len(payload) % 4) % 4)
        data = pack_rtcp_packet(RTCP_RTPFB, RTCP_RTPFB_TWCC, payload)

        packets = RtcpPacket.parse(data)
        self.assertEqual(len(packets), 1)
        parsed = self.ensureIsInstance(packets[0], RtcpTwccPacket)
        self.assertEqual(parsed.base_sequence_number, 0)
        self.assertEqual(parsed.packet_status_count, 3)
        self.assertEqual(len(parsed.packet_results), 3)

        # All received
        received = sum(1 for _, d in parsed.packet_results if d is not None)
        lost = sum(1 for _, d in parsed.packet_results if d is None)
        self.assertEqual(received, 3)
        self.assertEqual(lost, 0)

        # Vector 2: Mixed received and lost
        payload2 = pack("!LL", 1, 2)
        payload2 += pack("!HH", 10, 5)
        payload2 += pack("!BBB", 0, 0, 1)  # ref_time = 1
        payload2 += pack("!B", 1)

        # Status vector with 2-bit symbols: received, lost, received, lost, received
        # = 01 00 01 00 01 00 00 (pad to 7 symbols)
        symbols = 0b01000100010000
        chunk = 0xC000 | symbols
        payload2 += pack("!H", chunk)
        # Deltas for 3 received: 4, 4, 4 ticks (1000us each)
        payload2 += pack("!BBB", 4, 4, 4)
        payload2 += b"\x00" * ((4 - len(payload2) % 4) % 4)
        data2 = pack_rtcp_packet(RTCP_RTPFB, RTCP_RTPFB_TWCC, payload2)

        packets2 = RtcpPacket.parse(data2)
        parsed2 = self.ensureIsInstance(packets2[0], RtcpTwccPacket)
        self.assertEqual(parsed2.packet_status_count, 5)
        received2 = sum(1 for _, d in parsed2.packet_results if d is not None)
        lost2 = sum(1 for _, d in parsed2.packet_results if d is None)
        self.assertEqual(received2, 3)
        self.assertEqual(lost2, 2)

        # Vector 3: Large deltas
        payload3 = pack("!LL", 1, 2)
        payload3 += pack("!HH", 0, 2)
        payload3 += pack("!BBB", 0, 0, 0)
        payload3 += pack("!B", 0)
        # Run-length: status=2 (large delta), run=2
        payload3 += pack("!H", (2 << 13) | 2)
        # Large deltas: 1000 ticks (250ms), -500 ticks (-125ms)
        payload3 += pack("!hh", 1000, -500)
        payload3 += b"\x00" * ((4 - len(payload3) % 4) % 4)
        data3 = pack_rtcp_packet(RTCP_RTPFB, RTCP_RTPFB_TWCC, payload3)

        packets3 = RtcpPacket.parse(data3)
        parsed3 = self.ensureIsInstance(packets3[0], RtcpTwccPacket)
        self.assertEqual(parsed3.packet_status_count, 2)
        self.assertEqual(len(parsed3.packet_results), 2)
        # Both received
        self.assertIsNotNone(parsed3.packet_results[0][1])
        self.assertIsNotNone(parsed3.packet_results[1][1])

        # Vector 4: All lost
        payload4 = pack("!LL", 1, 2)
        payload4 += pack("!HH", 0, 5)
        payload4 += pack("!BBB", 0, 0, 0)
        payload4 += pack("!B", 0)
        # Run-length: status=0 (not received), run=5
        payload4 += pack("!H", 5)
        # No deltas, pad to 4-byte boundary
        payload4 += b"\x00" * ((4 - len(payload4) % 4) % 4)
        data4 = pack_rtcp_packet(RTCP_RTPFB, RTCP_RTPFB_TWCC, payload4)

        packets4 = RtcpPacket.parse(data4)
        parsed4 = self.ensureIsInstance(packets4[0], RtcpTwccPacket)
        self.assertEqual(parsed4.packet_status_count, 5)
        lost4 = sum(1 for _, d in parsed4.packet_results if d is None)
        self.assertEqual(lost4, 5)

        # Vector 5: Single packet
        payload5 = pack("!LL", 100, 200)
        payload5 += pack("!HH", 65535, 1)
        payload5 += pack("!BBB", 0, 0, 10)
        payload5 += pack("!B", 255)
        payload5 += pack("!H", (1 << 13) | 1)
        payload5 += pack("!B", 0)  # delta=0
        payload5 += b"\x00" * ((4 - len(payload5) % 4) % 4)
        data5 = pack_rtcp_packet(RTCP_RTPFB, RTCP_RTPFB_TWCC, payload5)

        packets5 = RtcpPacket.parse(data5)
        parsed5 = self.ensureIsInstance(packets5[0], RtcpTwccPacket)
        self.assertEqual(parsed5.ssrc, 100)
        self.assertEqual(parsed5.media_ssrc, 200)
        self.assertEqual(parsed5.base_sequence_number, 65535)
        self.assertEqual(parsed5.feedback_packet_count, 255)
        self.assertEqual(len(parsed5.packet_results), 1)
        self.assertIsNotNone(parsed5.packet_results[0][1])
