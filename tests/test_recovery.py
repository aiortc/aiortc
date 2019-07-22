from unittest import TestCase

from aioquic import tls
from aioquic.quic.packet import PACKET_TYPE_INITIAL
from aioquic.quic.packet_builder import QuicSentPacket
from aioquic.quic.recovery import QuicPacketRecovery, QuicPacketSpace


def send_probe():
    pass


class QuicPacketRecoveryTest(TestCase):
    def setUp(self):
        self.INITIAL_SPACE = QuicPacketSpace()
        self.HANDSHAKE_SPACE = QuicPacketSpace()
        self.ONE_RTT_SPACE = QuicPacketSpace()

        self.recovery = QuicPacketRecovery(
            is_client_without_1rtt=False, send_probe=send_probe
        )
        self.recovery.spaces = [
            self.INITIAL_SPACE,
            self.HANDSHAKE_SPACE,
            self.ONE_RTT_SPACE,
        ]

    def test_discard_space(self):
        self.recovery.discard_space(self.INITIAL_SPACE)

    def test_on_packet_lost_crypto(self):
        packet = QuicSentPacket(
            epoch=tls.Epoch.INITIAL,
            in_flight=True,
            is_ack_eliciting=True,
            is_crypto_packet=True,
            packet_number=0,
            packet_type=PACKET_TYPE_INITIAL,
            sent_bytes=1280,
            sent_time=123.45,
        )
        space = self.INITIAL_SPACE

        self.recovery.on_packet_sent(packet, space)
        self.assertEqual(self.recovery.bytes_in_flight, 1280)
        self.assertEqual(space.ack_eliciting_in_flight, 1)
        self.assertEqual(len(space.sent_packets), 1)

        self.recovery.on_packet_lost(packet, space)
        self.assertEqual(self.recovery.bytes_in_flight, 0)
        self.assertEqual(space.ack_eliciting_in_flight, 0)
        self.assertEqual(len(space.sent_packets), 0)
