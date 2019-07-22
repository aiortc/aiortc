import binascii
from unittest import TestCase

from aioquic.buffer import Buffer, BufferReadError
from aioquic.quic import packet
from aioquic.quic.packet import (
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_RETRY,
    QuicProtocolVersion,
    QuicTransportParameters,
    decode_packet_number,
    encode_quic_version_negotiation,
    pull_quic_header,
    pull_quic_transport_parameters,
    push_quic_transport_parameters,
)

from .utils import load


class PacketTest(TestCase):
    def test_decode_packet_number(self):
        # expected = 0
        for i in range(0, 256):
            self.assertEqual(decode_packet_number(i, 8, expected=0), i)

        # expected = 128
        self.assertEqual(decode_packet_number(0, 8, expected=128), 256)
        for i in range(1, 256):
            self.assertEqual(decode_packet_number(i, 8, expected=128), i)

        # expected = 129
        self.assertEqual(decode_packet_number(0, 8, expected=129), 256)
        self.assertEqual(decode_packet_number(1, 8, expected=129), 257)
        for i in range(2, 256):
            self.assertEqual(decode_packet_number(i, 8, expected=129), i)

        # expected = 256
        for i in range(0, 128):
            self.assertEqual(decode_packet_number(i, 8, expected=256), 256 + i)
        for i in range(129, 256):
            self.assertEqual(decode_packet_number(i, 8, expected=256), i)

    def test_pull_empty(self):
        buf = Buffer(data=b"")
        with self.assertRaises(BufferReadError):
            pull_quic_header(buf, host_cid_length=8)

    def test_pull_initial_client(self):
        buf = Buffer(data=load("initial_client.bin"))
        header = pull_quic_header(buf, host_cid_length=8)
        self.assertTrue(header.is_long_header)
        self.assertEqual(header.version, QuicProtocolVersion.DRAFT_22)
        self.assertEqual(header.packet_type, PACKET_TYPE_INITIAL)
        self.assertEqual(header.destination_cid, binascii.unhexlify("858b39368b8e3c6e"))
        self.assertEqual(header.source_cid, b"")
        self.assertEqual(header.original_destination_cid, b"")
        self.assertEqual(header.token, b"")
        self.assertEqual(header.rest_length, 1262)
        self.assertEqual(buf.tell(), 18)

    def test_pull_initial_server(self):
        buf = Buffer(data=load("initial_server.bin"))
        header = pull_quic_header(buf, host_cid_length=8)
        self.assertTrue(header.is_long_header)
        self.assertEqual(header.version, QuicProtocolVersion.DRAFT_22)
        self.assertEqual(header.packet_type, PACKET_TYPE_INITIAL)
        self.assertEqual(header.destination_cid, b"")
        self.assertEqual(header.source_cid, binascii.unhexlify("195c68344e28d479"))
        self.assertEqual(header.original_destination_cid, b"")
        self.assertEqual(header.token, b"")
        self.assertEqual(header.rest_length, 184)
        self.assertEqual(buf.tell(), 18)

    def test_pull_retry(self):
        buf = Buffer(data=load("retry.bin"))
        header = pull_quic_header(buf, host_cid_length=8)
        self.assertTrue(header.is_long_header)
        self.assertEqual(header.version, QuicProtocolVersion.DRAFT_22)
        self.assertEqual(header.packet_type, PACKET_TYPE_RETRY)
        self.assertEqual(header.destination_cid, binascii.unhexlify("fee746dfde699d61"))
        self.assertEqual(header.source_cid, binascii.unhexlify("59aa0942fd2f11e9"))
        self.assertEqual(
            header.original_destination_cid, binascii.unhexlify("d61e7448e0d63dff")
        )
        self.assertEqual(
            header.token,
            binascii.unhexlify(
                "5282f57f85a1a5c50de5aac2ff7dba43ff34524737099ec41c4b8e8c76734f935e8efd51177dbbe764"
            ),
        )
        self.assertEqual(header.rest_length, 0)
        self.assertEqual(buf.tell(), 73)

    def test_pull_version_negotiation(self):
        buf = Buffer(data=load("version_negotiation.bin"))
        header = pull_quic_header(buf, host_cid_length=8)
        self.assertTrue(header.is_long_header)
        self.assertEqual(header.version, QuicProtocolVersion.NEGOTIATION)
        self.assertEqual(header.packet_type, None)
        self.assertEqual(header.destination_cid, binascii.unhexlify("9aac5a49ba87a849"))
        self.assertEqual(header.source_cid, binascii.unhexlify("f92f4336fa951ba1"))
        self.assertEqual(header.original_destination_cid, b"")
        self.assertEqual(header.token, b"")
        self.assertEqual(header.rest_length, 8)
        self.assertEqual(buf.tell(), 23)

    def test_pull_long_header_no_fixed_bit(self):
        buf = Buffer(data=b"\x80\xff\x00\x00\x11\x00\x00")
        with self.assertRaises(ValueError) as cm:
            pull_quic_header(buf, host_cid_length=8)
        self.assertEqual(str(cm.exception), "Packet fixed bit is zero")

    def test_pull_long_header_too_short(self):
        buf = Buffer(data=b"\xc0\x00")
        with self.assertRaises(BufferReadError):
            pull_quic_header(buf, host_cid_length=8)

    def test_pull_short_header(self):
        buf = Buffer(data=load("short_header.bin"))
        header = pull_quic_header(buf, host_cid_length=8)
        self.assertFalse(header.is_long_header)
        self.assertEqual(header.version, None)
        self.assertEqual(header.packet_type, 0x50)
        self.assertEqual(header.destination_cid, binascii.unhexlify("f45aa7b59c0e1ad6"))
        self.assertEqual(header.source_cid, b"")
        self.assertEqual(header.original_destination_cid, b"")
        self.assertEqual(header.token, b"")
        self.assertEqual(header.rest_length, 12)
        self.assertEqual(buf.tell(), 9)

    def test_pull_short_header_no_fixed_bit(self):
        buf = Buffer(data=b"\x00")
        with self.assertRaises(ValueError) as cm:
            pull_quic_header(buf, host_cid_length=8)
        self.assertEqual(str(cm.exception), "Packet fixed bit is zero")

    def test_encode_quic_version_negotiation(self):
        data = encode_quic_version_negotiation(
            destination_cid=binascii.unhexlify("9aac5a49ba87a849"),
            source_cid=binascii.unhexlify("f92f4336fa951ba1"),
            supported_versions=[0x45474716, QuicProtocolVersion.DRAFT_22],
        )
        self.assertEqual(data[1:], load("version_negotiation.bin")[1:])


class ParamsTest(TestCase):
    maxDiff = None

    def test_params(self):
        data = binascii.unhexlify(
            "004700020010cc2fd6e7d97a53ab5be85b28d75c80080008000106000100026"
            "710000600048000ffff000500048000ffff000400048005fffa000a00010300"
            "0b0001190003000247e4"
        )

        # parse
        buf = Buffer(data=data)
        params = pull_quic_transport_parameters(buf)
        self.assertEqual(
            params,
            QuicTransportParameters(
                idle_timeout=10000,
                stateless_reset_token=b"\xcc/\xd6\xe7\xd9zS\xab[\xe8[(\xd7\\\x80\x08",
                max_packet_size=2020,
                initial_max_data=393210,
                initial_max_stream_data_bidi_local=65535,
                initial_max_stream_data_bidi_remote=65535,
                initial_max_stream_data_uni=None,
                initial_max_streams_bidi=6,
                initial_max_streams_uni=None,
                ack_delay_exponent=3,
                max_ack_delay=25,
            ),
        )

        # serialize
        buf = Buffer(capacity=len(data))
        push_quic_transport_parameters(buf, params)
        self.assertEqual(len(buf.data), len(data))

    def test_params_disable_migration(self):
        data = binascii.unhexlify("0004000c0000")

        # parse
        buf = Buffer(data=data)
        params = pull_quic_transport_parameters(buf)
        self.assertEqual(params, QuicTransportParameters(disable_migration=True))

        # serialize
        buf = Buffer(capacity=len(data))
        push_quic_transport_parameters(buf, params)
        self.assertEqual(buf.data, data)

    def test_params_unknown(self):
        # fb.mvfst.net sends a proprietary parameter 65280
        data = binascii.unhexlify(
            "006400050004800104000006000480010400000700048001040000040004801"
            "0000000080008c0000000ffffffff00090008c0000000ffffffff0001000480"
            "00ea60000a00010300030002500000020010616161616262626263636363646"
            "46464ff00000100"
        )

        # parse
        buf = Buffer(data=data)
        params = pull_quic_transport_parameters(buf)
        self.assertEqual(
            params,
            QuicTransportParameters(
                idle_timeout=60000,
                stateless_reset_token=b"aaaabbbbccccdddd",
                max_packet_size=4096,
                initial_max_data=1048576,
                initial_max_stream_data_bidi_local=66560,
                initial_max_stream_data_bidi_remote=66560,
                initial_max_stream_data_uni=66560,
                initial_max_streams_bidi=4294967295,
                initial_max_streams_uni=4294967295,
                ack_delay_exponent=3,
            ),
        )


class FrameTest(TestCase):
    def test_ack_frame(self):
        data = b"\x00\x02\x00\x00"

        # parse
        buf = Buffer(data=data)
        rangeset, delay = packet.pull_ack_frame(buf)
        self.assertEqual(list(rangeset), [range(0, 1)])
        self.assertEqual(delay, 2)

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_ack_frame(buf, rangeset, delay)
        self.assertEqual(buf.data, data)

    def test_ack_frame_with_one_range(self):
        data = b"\x02\x02\x01\x00\x00\x00"

        # parse
        buf = Buffer(data=data)
        rangeset, delay = packet.pull_ack_frame(buf)
        self.assertEqual(list(rangeset), [range(0, 1), range(2, 3)])
        self.assertEqual(delay, 2)

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_ack_frame(buf, rangeset, delay)
        self.assertEqual(buf.data, data)

    def test_ack_frame_with_one_range_2(self):
        data = b"\x05\x02\x01\x00\x00\x03"

        # parse
        buf = Buffer(data=data)
        rangeset, delay = packet.pull_ack_frame(buf)
        self.assertEqual(list(rangeset), [range(0, 4), range(5, 6)])
        self.assertEqual(delay, 2)

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_ack_frame(buf, rangeset, delay)
        self.assertEqual(buf.data, data)

    def test_ack_frame_with_one_range_3(self):
        data = b"\x05\x02\x01\x00\x01\x02"

        # parse
        buf = Buffer(data=data)
        rangeset, delay = packet.pull_ack_frame(buf)
        self.assertEqual(list(rangeset), [range(0, 3), range(5, 6)])
        self.assertEqual(delay, 2)

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_ack_frame(buf, rangeset, delay)
        self.assertEqual(buf.data, data)

    def test_ack_frame_with_two_ranges(self):
        data = b"\x04\x02\x02\x00\x00\x00\x00\x00"

        # parse
        buf = Buffer(data=data)
        rangeset, delay = packet.pull_ack_frame(buf)
        self.assertEqual(list(rangeset), [range(0, 1), range(2, 3), range(4, 5)])
        self.assertEqual(delay, 2)

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_ack_frame(buf, rangeset, delay)
        self.assertEqual(buf.data, data)

    def test_new_token(self):
        data = binascii.unhexlify("080102030405060708")

        # parse
        buf = Buffer(data=data)
        token = packet.pull_new_token_frame(buf)
        self.assertEqual(token, binascii.unhexlify("0102030405060708"))

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_new_token_frame(buf, token)
        self.assertEqual(buf.data, data)

    def test_new_connection_id(self):
        data = binascii.unhexlify(
            "0200117813f3d9e45e0cacbb491b4b66b039f20406f68fede38ec4c31aba8ab1245244e8"
        )

        # parse
        buf = Buffer(data=data)
        frame = packet.pull_new_connection_id_frame(buf)
        self.assertEqual(
            frame,
            (
                2,
                0,
                binascii.unhexlify("7813f3d9e45e0cacbb491b4b66b039f204"),
                binascii.unhexlify("06f68fede38ec4c31aba8ab1245244e8"),
            ),
        )

        # serialize
        buf = Buffer(capacity=len(data))
        packet.push_new_connection_id_frame(buf, *frame)
        self.assertEqual(buf.data, data)

    def test_transport_close(self):
        data = binascii.unhexlify("0a0212696c6c6567616c2041434b206672616d6500")

        # parse
        buf = Buffer(data=data)
        frame = packet.pull_transport_close_frame(buf)
        self.assertEqual(frame, (10, 2, "illegal ACK frame\x00"))

    def test_application_close(self):
        data = binascii.unhexlify("0008676f6f6462796500")

        # parse
        buf = Buffer(data=data)
        frame = packet.pull_application_close_frame(buf)
        self.assertEqual(frame, (0, "goodbye\x00"))

    def test_application_close_not_utf8(self):
        data = binascii.unhexlify("0008676f6f6462798200")

        # parse
        buf = Buffer(data=data)
        frame = packet.pull_application_close_frame(buf)
        self.assertEqual(frame, (0, ""))
