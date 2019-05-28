from unittest import TestCase

from aioquic.connection import QuicPacketBuilder
from aioquic.crypto import CryptoPair
from aioquic.packet import (
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_ONE_RTT,
    QuicFrameType,
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
            pad_first_datagram=True,
            peer_cid=bytes(8),
            peer_token=b"",
            spin_bit=False,
            version=QuicProtocolVersion.DRAFT_20,
        )
        crypto = CryptoPair()
        crypto.setup_initial(bytes(8), is_client=True)

        # INITIAL, fully padded
        builder.start_packet(PACKET_TYPE_INITIAL, crypto)
        self.assertEqual(builder.remaining_space, 1237)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(100))
        self.assertTrue(builder.end_packet())

        # check builder state
        self.assertEqual(builder.buffer.tell(), 1280)
        self.assertEqual(builder.packet_number, 1)

        # check datagrams
        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 1280)

    def test_long_header_then_short_header(self):
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

        # INITIAL, fully padded
        builder.start_packet(PACKET_TYPE_INITIAL, crypto)
        self.assertEqual(builder.remaining_space, 1237)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        # ONE_RTT, fully padded
        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        # check builder
        self.assertTrue(builder.ack_eliciting)
        self.assertEqual(builder.buffer.tell(), 0)

        # check datagrams
        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 2)
        self.assertEqual(len(datagrams[0]), 1280)
        self.assertEqual(len(datagrams[1]), 1280)

    def test_long_header_then_long_header(self):
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

        # INITIAL
        builder.start_packet(PACKET_TYPE_INITIAL, crypto)
        self.assertEqual(builder.remaining_space, 1237)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(199))
        self.assertEqual(builder.buffer.tell(), 227)
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 243)

        # HANDSHAKE
        builder.start_packet(PACKET_TYPE_HANDSHAKE, crypto)
        self.assertEqual(builder.buffer.tell(), 269)
        self.assertEqual(builder.remaining_space, 995)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(299))
        self.assertEqual(builder.buffer.tell(), 569)
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 585)

        # ONE_RTT
        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 668)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(299))

        # check builder
        self.assertTrue(builder.ack_eliciting)
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)

        # check datagrams
        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 912)

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

        # check builder
        self.assertFalse(builder.ack_eliciting)
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

        # ONE_RTT, fully padded
        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        # check builder
        self.assertTrue(builder.ack_eliciting)
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 1)

        # check datagrams
        datagrams = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 1280)
