from unittest import TestCase

from aioquic.crypto import CryptoPair
from aioquic.packet import (
    PACKET_TYPE_HANDSHAKE,
    PACKET_TYPE_INITIAL,
    PACKET_TYPE_ONE_RTT,
    QuicFrameType,
    QuicProtocolVersion,
    push_bytes,
)
from aioquic.packet_builder import QuicPacketBuilder, QuicSentPacket
from aioquic.tls import Epoch


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
        self.assertEqual(builder.remaining_space, 1237)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(100))
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
        self.assertEqual(builder.remaining_space, 1237)
        builder.start_frame(QuicFrameType.CRYPTO)
        push_bytes(builder.buffer, bytes(builder.remaining_space))
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 1280)

        # ONE_RTT, fully padded
        builder.start_packet(PACKET_TYPE_ONE_RTT, crypto)
        self.assertEqual(builder.remaining_space, 1253)
        builder.start_frame(QuicFrameType.STREAM_BASE)
        push_bytes(builder.buffer, bytes(builder.remaining_space))
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
                    sent_bytes=1280,
                ),
                QuicSentPacket(
                    epoch=Epoch.ONE_RTT,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=False,
                    packet_number=1,
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
        self.assertTrue(builder.end_packet())
        self.assertEqual(builder.buffer.tell(), 0)

        # check datagrams
        datagrams, packets = builder.flush()
        self.assertEqual(len(datagrams), 1)
        self.assertEqual(len(datagrams[0]), 912)
        self.assertEqual(
            packets,
            [
                QuicSentPacket(
                    epoch=Epoch.INITIAL,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=0,
                    sent_bytes=243,
                ),
                QuicSentPacket(
                    epoch=Epoch.HANDSHAKE,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=1,
                    sent_bytes=342,
                ),
                QuicSentPacket(
                    epoch=Epoch.ONE_RTT,
                    in_flight=True,
                    is_ack_eliciting=True,
                    is_crypto_packet=True,
                    packet_number=2,
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
        push_bytes(builder.buffer, bytes(builder.remaining_space))
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
                    sent_bytes=1280,
                )
            ],
        )
