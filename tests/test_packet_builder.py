from unittest import TestCase

from aioquic.connection import QuicPacketBuilder
from aioquic.crypto import CryptoPair
from aioquic.packet import (
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_ONE_RTT,
    QuicProtocolVersion,
    push_bytes,
)


class QuicPacketBuilderTest(TestCase):
    def test_long_header_empty(self):
        builder = QuicPacketBuilder(
            host_cid=bytes(8),
            packet_number=0,
            peer_cid=bytes(8),
            peer_token=b"",
            spin_bit=False,
            version=QuicProtocolVersion.DRAFT_20,
        )
        crypto = CryptoPair()

        builder.start_packet(PACKET_TYPE_INITIAL, crypto)
        self.assertEqual(builder.remaining_space, 1237)

        # nothing to write

        self.assertFalse(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 0)

        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 0)

    def test_long_header_padding(self):
        builder = QuicPacketBuilder(
            host_cid=bytes(8),
            packet_number=0,
            peer_cid=bytes(8),
            peer_token=b"",
            spin_bit=False,
            version=QuicProtocolVersion.DRAFT_20,
        )
        crypto = CryptoPair()
        crypto.setup_initial(bytes(8), is_client=True)

        builder.start_packet(PACKET_TYPE_INITIAL, crypto)
        self.assertEqual(builder.remaining_space, 1237)

        # padding only
        push_bytes(builder.buffer, bytes(builder.remaining_space))

        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 1)

        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 1280)

    def test_short_header_empty(self):
        builder = QuicPacketBuilder(
            host_cid=bytes(8),
            packet_number=0,
            peer_cid=bytes(8),
            peer_token=b"",
            spin_bit=False,
            version=QuicProtocolVersion.DRAFT_20,
        )
        crypto = CryptoPair()

        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)

        # nothing to write

        self.assertFalse(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 0)

        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 0)

    def test_short_header_padding(self):
        builder = QuicPacketBuilder(
            host_cid=bytes(8),
            packet_number=0,
            peer_cid=bytes(8),
            peer_token=b"",
            spin_bit=False,
            version=QuicProtocolVersion.DRAFT_20,
        )
        crypto = CryptoPair()
        crypto.setup_initial(bytes(8), is_client=True)

        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)

        # padding only
        push_bytes(builder.buffer, bytes(builder.remaining_space))

        data = builder.end_packet()
        self.assertTrue(data)
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 1)

        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 1280)
