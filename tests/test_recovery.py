from unittest import TestCase

from aioquic import tls
from aioquic.quic.packet import PACKET_TYPE_INITIAL
from aioquic.quic.packet_builder import QuicSentPacket
from aioquic.quic.recovery import QuicPacketRecovery, QuicPacketSpace, QuicRttMonitor


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


class QuicRttMonitorTest(TestCase):
    def test_monitor(self):
        monitor = QuicRttMonitor()

        self.assertFalse(monitor.is_rtt_increasing(rtt=10, now=1000))
        self.assertEqual(monitor._samples, [10, 0.0, 0.0, 0.0, 0.0])
        self.assertFalse(monitor._ready)

        # not taken into account
        self.assertFalse(monitor.is_rtt_increasing(rtt=11, now=1000))
        self.assertEqual(monitor._samples, [10, 0.0, 0.0, 0.0, 0.0])
        self.assertFalse(monitor._ready)

        self.assertFalse(monitor.is_rtt_increasing(rtt=11, now=1001))
        self.assertEqual(monitor._samples, [10, 11, 0.0, 0.0, 0.0])
        self.assertFalse(monitor._ready)

        self.assertFalse(monitor.is_rtt_increasing(rtt=12, now=1002))
        self.assertEqual(monitor._samples, [10, 11, 12, 0.0, 0.0])
        self.assertFalse(monitor._ready)

        self.assertFalse(monitor.is_rtt_increasing(rtt=13, now=1003))
        self.assertEqual(monitor._samples, [10, 11, 12, 13, 0.0])
        self.assertFalse(monitor._ready)

        # we now have enough samples
        self.assertFalse(monitor.is_rtt_increasing(rtt=14, now=1004))
        self.assertEqual(monitor._samples, [10, 11, 12, 13, 14])
        self.assertTrue(monitor._ready)

        self.assertFalse(monitor.is_rtt_increasing(rtt=20, now=1005))
        self.assertEqual(monitor._increases, 0)

        self.assertFalse(monitor.is_rtt_increasing(rtt=30, now=1006))
        self.assertEqual(monitor._increases, 0)

        self.assertFalse(monitor.is_rtt_increasing(rtt=40, now=1007))
        self.assertEqual(monitor._increases, 0)

        self.assertFalse(monitor.is_rtt_increasing(rtt=50, now=1008))
        self.assertEqual(monitor._increases, 0)

        self.assertFalse(monitor.is_rtt_increasing(rtt=60, now=1009))
        self.assertEqual(monitor._increases, 1)

        self.assertFalse(monitor.is_rtt_increasing(rtt=70, now=1010))
        self.assertEqual(monitor._increases, 2)

        self.assertFalse(monitor.is_rtt_increasing(rtt=80, now=1011))
        self.assertEqual(monitor._increases, 3)

        self.assertFalse(monitor.is_rtt_increasing(rtt=90, now=1012))
        self.assertEqual(monitor._increases, 4)

        self.assertTrue(monitor.is_rtt_increasing(rtt=100, now=1013))
        self.assertEqual(monitor._increases, 5)
