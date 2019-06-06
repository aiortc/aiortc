import logging
import math
from typing import Callable, Dict, List, Optional

from .packet_builder import QuicDeliveryState, QuicSentPacket
from .rangeset import RangeSet

# loss detection
K_PACKET_THRESHOLD = 3
K_INITIAL_RTT = 0.5  # seconds
K_GRANULARITY = 0.001  # seconds
K_TIME_THRESHOLD = 9 / 8

# congestion control
K_MAX_DATAGRAM_SIZE = 1280
K_INITIAL_WINDOW = 10 * K_MAX_DATAGRAM_SIZE
K_MINIMUM_WINDOW = 2 * K_MAX_DATAGRAM_SIZE
K_LOSS_REDUCTION_FACTOR = 0.5


class QuicPacketSpace:
    def __init__(self) -> None:
        self.ack_queue = RangeSet()
        self.ack_required = False
        self.expected_packet_number = 0

        # sent packets and loss
        self.ack_eliciting_in_flight = 0
        self.largest_acked_packet = 0
        self.loss_time: Optional[float] = None
        self.sent_packets: Dict[int, QuicSentPacket] = {}

    def teardown(self) -> None:
        self.ack_eliciting_in_flight = 0
        self.loss_time = None
        self.sent_packets.clear()


class QuicPacketRecovery:
    """
    Packet loss and congestion controller.
    """

    def __init__(
        self,
        logger: logging.LoggerAdapter,
        get_time: Callable[[], float],
        send_probe: Callable[[], None],
        set_loss_detection_timer: Callable[[], None],
    ) -> None:
        self.ack_delay_exponent = 3
        self.max_ack_delay = 25  # ms
        self.spaces: List[QuicPacketSpace] = []

        # callbacks
        self._get_time = get_time
        self._logger = logger
        self._send_probe = send_probe
        self._set_loss_detection_timer = set_loss_detection_timer

        # loss detection
        self._crypto_count = 0
        self._pto_count = 0
        self._rtt_initialized = False
        self._rtt_latest = 0.0
        self._rtt_min = math.inf
        self._rtt_smoothed = 0.0
        self._rtt_variance = 0.0
        self._time_of_last_sent_ack_eliciting_packet = 0.0
        self._time_of_last_sent_crypto_packet = 0.0

        # congestion control
        self.bytes_in_flight = 0
        self.congestion_window = K_INITIAL_WINDOW
        self._congestion_recovery_start_time = 0.0
        self._ssthresh = math.inf

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

        lost_bytes = 0
        lost_largest_time = None
        space.loss_time = None
        for packet_number, packet in list(space.sent_packets.items()):
            if packet_number > space.largest_acked_packet:
                break

            if packet_number <= packet_threshold or packet.sent_time < time_threshold:
                # remove packet and update counters
                del space.sent_packets[packet_number]
                if packet.is_ack_eliciting:
                    space.ack_eliciting_in_flight -= 1
                if packet.in_flight:
                    lost_bytes += packet.sent_bytes
                    lost_largest_time = packet.sent_time

                # trigger callbacks
                for handler, args in packet.delivery_handlers:
                    handler(QuicDeliveryState.LOST, *args)
            else:
                packet_loss_time = packet.sent_time + loss_delay
                if space.loss_time is None or space.loss_time > packet_loss_time:
                    space.loss_time = packet_loss_time

        if lost_bytes:
            self.on_packets_lost(lost_bytes, lost_largest_time)

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
        if not next(
            (True for space in self.spaces if space.ack_eliciting_in_flight), False
        ):
            return None

        # PTO
        if not self._rtt_initialized:
            timeout = K_INITIAL_RTT
        else:
            timeout = (
                self._rtt_smoothed
                + max(4 * self._rtt_variance, K_GRANULARITY)
                + self.max_ack_delay / 1000
            ) * (2 ** self._pto_count)
        return self._time_of_last_sent_ack_eliciting_packet + timeout

    def get_probe_timeout(self) -> float:
        return (
            self._rtt_smoothed
            + max(4 * self._rtt_variance, K_GRANULARITY)
            + self.max_ack_delay / 1000
        )

    def on_ack_received(
        self, space: QuicPacketSpace, ack_rangeset: RangeSet, ack_delay_encoded: int
    ) -> None:
        """
        Update metrics as the result of an ACK being received.
        """
        ack_time = self._get_time()

        is_ack_eliciting = False
        largest_acked = ack_rangeset.bounds().stop - 1
        largest_newly_acked = None
        largest_sent_time = None

        if largest_acked > space.largest_acked_packet:
            space.largest_acked_packet = largest_acked

        for packet_number in sorted(space.sent_packets.keys()):
            if packet_number > largest_acked:
                break
            if packet_number in ack_rangeset:
                # remove packet and update counters
                packet = space.sent_packets.pop(packet_number)
                if packet.is_ack_eliciting:
                    is_ack_eliciting = True
                    space.ack_eliciting_in_flight -= 1
                if packet.in_flight:
                    self.on_packet_acked(packet)
                largest_newly_acked = packet_number
                largest_sent_time = packet.sent_time

                # trigger callbacks
                for handler, args in packet.delivery_handlers:
                    handler(QuicDeliveryState.ACKED, *args)

        # nothing to do if there are no newly acked packets
        if largest_newly_acked is None:
            return

        if largest_acked == largest_newly_acked and is_ack_eliciting:
            latest_rtt = ack_time - largest_sent_time

            # decode ACK delay into seconds
            ack_delay = max(
                (ack_delay_encoded << self.ack_delay_exponent) / 1000000,
                self.max_ack_delay / 1000,
            )

            # update RTT estimate, which cannot be < 1 ms
            self._rtt_latest = max(ack_time - largest_sent_time, 0.001)
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

        # arm the timer
        self._set_loss_detection_timer()

    def on_loss_detection_timeout(self) -> None:
        self._logger.info("Loss detection timeout triggered")
        loss_space = self.get_earliest_loss_time()
        if loss_space is not None:
            # detect loss and re-arm the timer
            self.detect_loss(loss_space)
            self._set_loss_detection_timer()
        else:
            # sending the probe will re-arm the timer
            self._pto_count += 1
            self._send_probe()

    def on_packet_acked(self, packet: QuicSentPacket) -> None:
        self.bytes_in_flight -= packet.sent_bytes

        # don't increase window in congestion recovery
        if packet.sent_time <= self._congestion_recovery_start_time:
            return

        if self.congestion_window < self._ssthresh:
            # slow start
            self.congestion_window += packet.sent_bytes
        else:
            # congestion avoidance
            self.congestion_window += (
                K_MAX_DATAGRAM_SIZE * packet.sent_bytes // self.congestion_window
            )

    def on_packet_sent(self, packet: QuicSentPacket, space: QuicPacketSpace) -> None:
        packet.sent_time = self._get_time()
        space.sent_packets[packet.packet_number] = packet

        if packet.is_ack_eliciting:
            space.ack_eliciting_in_flight += 1
        if packet.in_flight:
            if packet.is_crypto_packet:
                self._time_of_last_sent_crypto_packet = packet.sent_time
            if packet.is_ack_eliciting:
                self._time_of_last_sent_ack_eliciting_packet = packet.sent_time

            # add packet to bytes in flight
            self.bytes_in_flight += packet.sent_bytes

    def on_packets_lost(self, lost_bytes: int, lost_largest_time: float) -> None:
        # remove lost packets from bytes in flight
        self.bytes_in_flight -= lost_bytes

        # start a new congestion event if packet was sent after the
        # start of the previous congestion recovery period.
        if lost_largest_time > self._congestion_recovery_start_time:
            self._congestion_recovery_start_time = self._get_time()
            self.congestion_window = max(
                int(self.congestion_window * K_LOSS_REDUCTION_FACTOR), K_MINIMUM_WINDOW
            )
            self._ssthresh = self.congestion_window

        # TODO : collapse congestion window if persistent congestion
