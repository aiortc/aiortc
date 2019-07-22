from unittest import TestCase

from aioquic.quic.crypto import CryptoPair
from aioquic.quic.packet import (
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_ONE_RTT,
    QuicFrameType,
    QuicProtocolVersion,
)
from aioquic.quic.packet_builder import (
    QuicPacketBuilder,
    QuicPacketBuilderStop,
    QuicSentPacket,
)
from aioquic.tls import Epoch


def create_builder():
    return QuicPacketBuilder(
        host_cid=bytes(8),
        packet_number=0,
        peer_cid=bytes(8),
        peer_token=b"",
        spin_bit=False,
        version=QuicProtocolVersion.DRAFT_20,
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
        self.assertEqual(builder.remaining_space, 1236)

        # nothing to write

        self.assertFalse(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 0)

        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 0)
        self.assertEqual(packets, [])

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
        self.assertEqual(builder.remaining_space, 1236)
        builder.start_frame(QuicFrameType.CRYPTO)
        builder.buffer.push_bytes(bytes(100))
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 1280)

        # check datagrams
        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 1280)
        self.assertEqual(
            packets,
            [
                QuicSentPacket(
                    epoch=Epoch.INITIAL,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=0,
                    packet_type=PACKET_TYPE_INITIAL,
                    sent_bytes=1280,
                )
            ],
        )

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
        self.assertEqual(builder.remaining_space, 1236)
        builder.start_frame(QuicFrameType.CRYPTO)
        builder.buffer.push_bytes(bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 1280)

        # ONE_RTT, fully padded
        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)
        builder.start_frame(QuicFrameType.STREAM_BASE)
        builder.buffer.push_bytes(bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)

        # check datagrams
        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 2)
        self.assertEqual(len(datagrams[0]), 1280)
        self.assertEqual(len(datagrams[1]), 1280)
        self.assertEqual(
            packets,
            [
                QuicSentPacket(
                    epoch=Epoch.INITIAL,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=0,
                    packet_type=PACKET_TYPE_INITIAL,
                    sent_bytes=1280,
                ),
                QuicSentPacket(
                    epoch=Epoch.ONE_RTT,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=False,
                    packet_number=1,
                    packet_type=PACKET_TYPE_ONE_RTT,
                    sent_bytes=1280,
                ),
            ],
        )

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
        self.assertEqual(builder.remaining_space, 1236)
        builder.start_frame(QuicFrameType.CRYPTO)
        builder.buffer.push_bytes(bytes(199))
        self.assertEqual(builder.buffer.tell(), 228)
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 244)

        # HANDSHAKE
        builder.start_packet(PACKET_TYPE_HANDSHAKE, crypto)
        self.assertEqual(builder.buffer.tell(), 271)
        self.assertEqual(builder.remaining_space, 993)
        builder.start_frame(QuicFrameType.CRYPTO)
        builder.buffer.push_bytes(bytes(299))
        self.assertEqual(builder.buffer.tell(), 571)
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 587)

        # ONE_RTT
        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 666)
        builder.start_frame(QuicFrameType.CRYPTO)
        builder.buffer.push_bytes(bytes(299))
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)

        # check datagrams
        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 914)
        self.assertEqual(
            packets,
            [
                QuicSentPacket(
                    epoch=Epoch.INITIAL,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=0,
                    packet_type=PACKET_TYPE_INITIAL,
                    sent_bytes=244,
                ),
                QuicSentPacket(
                    epoch=Epoch.HANDSHAKE,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=1,
                    packet_type=PACKET_TYPE_HANDSHAKE,
                    sent_bytes=343,
                ),
                QuicSentPacket(
                    epoch=Epoch.ONE_RTT,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=2,
                    packet_type=PACKET_TYPE_ONE_RTT,
                    sent_bytes=327,
                ),
            ],
        )

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
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 0)

        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 0)
        self.assertEqual(packets, [])

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
        builder.buffer.push_bytes(bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        # check builder
        self.assertEqual(builder.buffer.tell(), 0)
        self.assertEqual(builder.packet_number, 1)

        # check datagrams
        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 1280)
        self.assertEqual(
            packets,
            [
                QuicSentPacket(
                    epoch=Epoch.ONE_RTT,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=0,
                    packet_type=PACKET_TYPE_ONE_RTT,
                    sent_bytes=1280,
                )
            ],
        )

    def test_short_header_max_total_bytes_1(self):
        """
        max_total_bytes doesn't allow any packets.
        """
        builder = create_builder()
        builder.max_total_bytes = 11

        crypto = CryptoPair()
        crypto.setup_initial(bytes(8), is_client=True)

        with self.assertRaises(QuicPacketBuilderStop):
            builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)

    def test_short_header_max_total_bytes_2(self):
        """
        max_total_bytes allows a short packet.
        """
        builder = create_builder()
        builder.max_total_bytes = 800

        crypto = CryptoPair()
        crypto.setup_initial(bytes(8), is_client=True)

        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 773)
        builder.buffer.push_bytes(bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        with self.assertRaises(QuicPacketBuilderStop):
            builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)

    def test_short_header_max_total_bytes_3(self):
        builder = create_builder()
        builder.max_total_bytes = 2000

        crypto = CryptoPair()
        crypto.setup_initial(bytes(8), is_client=True)

        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)
        builder.buffer.push_bytes(bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 693)
        builder.buffer.push_bytes(bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())

        with self.assertRaises(QuicPacketBuilderStop):
            builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
