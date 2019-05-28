import math
from typing import Callable, Dict, List, Optional

from .packet_builder import QuicDeliveryState, QuicSentPacket
from .rangeset import RangeSet

K_PACKET_THRESHOLD = 3
K_INITIAL_RTT = 0.5  # seconds
K_GRANULARITY = 0.001  # seconds
K_TIME_THRESHOLD = 9 / 8


class QuicPacketSpace:
    def __init__(self) -> None:
        self.ack_queue = RangeSet()
        self.ack_required = False
        self.expected_packet_number = 0

        # sent packets and loss
        self.largest_acked_packet = 0
        self.loss_time: Optional[float] = None
        self.sent_packets: Dict[int, QuicSentPacket] = {}


class QuicPacketLoss:
    def __init__(
        self, get_time: Callable[[], float], send_probe: Callable[[], None]
    ) -> None:
        self.ack_delay_exponent = 3
        self.max_ack_delay = 25  # ms
        self.spaces: List[QuicPacketSpace] = []

        # callbacks
        self._get_time = get_time
        self._send_probe = send_probe

        self._crypto_count = 0
        self._pto_count = 0

        self._rtt_initialized = False
        self._rtt_latest = 0.0
        self._rtt_min = math.inf
        self._rtt_smoothed = 0.0
        self._rtt_variance = 0.0

        self._time_of_last_sent_ack_eliciting_packet = 0.0
        self._time_of_last_sent_crypto_packet = 0.0

    def detect_loss(self, space: QuicPacketSpace) -> None:
        """
        Check whether any packets should be declared lost.
        """
        loss_delay = K_TIME_THRESHOLD * (
            max(self._rtt_latest, self._rtt_smoothed)
            if self._rtt_initialized
            else K_INITIAL_RTT
        )
        packet_threshold = space.largest_acked_packet - K_PACKET_THRESHOLD
        time_threshold = self._get_time() - loss_delay

        space.loss_time = None
        for packet_number, packet in list(space.sent_packets.items()):
            if packet_number > space.largest_acked_packet:
                continue

            if packet_number <= packet_threshold or packet.sent_time < time_threshold:
                for handler, args in packet.delivery_handlers:
                    handler(QuicDeliveryState.LOST, *args)
                del space.sent_packets[packet_number]
            else:
                packet_loss_time = packet.sent_time + loss_delay
                if space.loss_time is None or space.loss_time > packet_loss_time:
                    space.loss_time = packet_loss_time

    def get_earliest_loss_time(self) -> Optional[QuicPacketSpace]:
        loss_space = None
        for space in self.spaces:
            if space.loss_time is not None and (
                loss_space is None or space.loss_time < loss_space.loss_time
            ):
                loss_space = space
        return loss_space

    def get_loss_detection_time(self) -> float:
        loss_space = self.get_earliest_loss_time()
        if loss_space is not None:
            return loss_space.loss_time

        # check there are ACK-eliciting packets in flight
        ack_eliciting_in_flight = False
        for space in self.spaces:
            for packet in space.sent_packets.values():
                if packet.is_ack_eliciting:
                    ack_eliciting_in_flight = True
                    break
        if not ack_eliciting_in_flight:
            return None

        # PTO
        if not self._rtt_initialized:
            timeout = 0.5
        else:
            timeout = (
                self._rtt_smoothed
                + max(4 * self._rtt_variance, K_GRANULARITY)
                + self.max_ack_delay / 1000
            ) * (2 ** self._pto_count)
        return self._time_of_last_sent_ack_eliciting_packet + timeout

    def on_ack_received(
        self,
        space: QuicPacketSpace,
        is_ack_eliciting: bool,
        largest_newly_acked: int,
        latest_rtt: float,
        ack_delay_encoded: int,
    ) -> None:
        """
        Update metrics as the result of an ACK being received.
        """
        if largest_newly_acked > space.largest_acked_packet:
            space.largest_acked_packet = largest_newly_acked

        if is_ack_eliciting:
            # decode ACK delay into seconds
            ack_delay = max(
                (ack_delay_encoded << self.ack_delay_exponent) / 1000000,
                self.max_ack_delay / 1000,
            )

            # update RTT estimate, which cannot be < 1 ms
            self._rtt_latest = max(latest_rtt, 0.001)
            if self._rtt_latest < self._rtt_min:
                self._rtt_min = self._rtt_latest
            if self._rtt_latest > self._rtt_min + ack_delay:
                self._rtt_latest -= ack_delay

            if not self._rtt_initialized:
                self._rtt_initialized = True
                self._rtt_variance = latest_rtt / 2
                self._rtt_smoothed = latest_rtt
            else:
                self._rtt_variance = 3 / 4 * self._rtt_variance + 1 / 4 * abs(
                    self._rtt_min - self._rtt_latest
                )
                self._rtt_smoothed = (
                    7 / 8 * self._rtt_smoothed + 1 / 8 * self._rtt_latest
                )

        self.detect_loss(space)

        self._crypto_count = 0
        self._pto_count = 0

    def on_loss_timeout(self) -> None:
        loss_space = self.get_earliest_loss_time()
        if loss_space is not None:
            self.detect_loss(loss_space)
        else:
            self._pto_count += 1
            self._send_probe()

    def on_packet_sent(self, packet: QuicSentPacket) -> None:
        if packet.is_crypto_packet:
            self._time_of_last_sent_crypto_packet = packet.sent_time
        if packet.is_ack_eliciting:
            self._time_of_last_sent_ack_eliciting_packet = packet.sent_time
