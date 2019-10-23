import math
from typing import Callable, Dict, List, Optional

from .logger import QuicLoggerTrace
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
        self.ack_at: Optional[float] = None
        self.ack_queue = RangeSet()
        self.expected_packet_number = 0
        self.largest_received_packet = 0

        # sent packets and loss
        self.ack_eliciting_in_flight = 0
        self.largest_acked_packet = 0
        self.loss_time: Optional[float] = None
        self.sent_packets: Dict[int, QuicSentPacket] = {}


class QuicPacketRecovery:
    """
    Packet loss and congestion controller.
    """

    def __init__(
        self,
        is_client_without_1rtt: bool,
        send_probe: Callable[[], None],
        quic_logger: Optional[QuicLoggerTrace] = None,
    ) -> None:
        self.is_client_without_1rtt = is_client_without_1rtt
        self.max_ack_delay = 0.025
        self.spaces: List[QuicPacketSpace] = []

        # callbacks
        self._quic_logger = quic_logger
        self._send_probe = send_probe

        # loss detection
        self._pto_count = 0
        self._rtt_initialized = False
        self._rtt_latest = 0.0
        self._rtt_min = math.inf
        self._rtt_smoothed = 0.0
        self._rtt_variance = 0.0
        self._time_of_last_sent_ack_eliciting_packet = 0.0

        # congestion control
        self.bytes_in_flight = 0
        self.congestion_window = K_INITIAL_WINDOW
        self._congestion_recovery_start_time = 0.0
        self._congestion_stash = 0
        self._rtt_monitor = QuicRttMonitor()
        self._ssthresh: Optional[int] = None

    def detect_loss(self, space: QuicPacketSpace, now: float) -> None:
        """
        Check whether any packets should be declared lost.
        """
        loss_delay = K_TIME_THRESHOLD * (
            max(self._rtt_latest, self._rtt_smoothed)
            if self._rtt_initialized
            else K_INITIAL_RTT
        )
        packet_threshold = space.largest_acked_packet - K_PACKET_THRESHOLD
        time_threshold = now - loss_delay

        lost_largest_time = None
        lost_packets = []
        space.loss_time = None
        for packet_number, packet in space.sent_packets.items():
            if packet_number > space.largest_acked_packet:
                break

            if packet_number <= packet_threshold or packet.sent_time <= time_threshold:
                lost_packets.append(packet)
            else:
                packet_loss_time = packet.sent_time + loss_delay
                if space.loss_time is None or space.loss_time > packet_loss_time:
                    space.loss_time = packet_loss_time

        for packet in lost_packets:
            # remove packet and update counters
            self.on_packet_lost(packet, space)
            if packet.in_flight:
                lost_largest_time = packet.sent_time

        if lost_largest_time is not None:
            self.on_packets_lost(lost_largest_time, now=now)

    def discard_space(self, space: QuicPacketSpace) -> None:
        assert space in self.spaces

        for packet in space.sent_packets.values():
            if packet.in_flight:
                self.on_packet_expired(packet)
        space.sent_packets.clear()

        space.ack_at = None
        space.ack_eliciting_in_flight = 0
        space.loss_time = None

    def get_earliest_loss_time(self) -> Optional[QuicPacketSpace]:
        loss_space = None
        for space in self.spaces:
            if space.loss_time is not None and (
                loss_space is None or space.loss_time < loss_space.loss_time
            ):
                loss_space = space
        return loss_space

    def get_loss_detection_time(self) -> float:
        # loss timer
        loss_space = self.get_earliest_loss_time()
        if loss_space is not None:
            return loss_space.loss_time

        # packet timer
        if (
            self.is_client_without_1rtt
            or sum(space.ack_eliciting_in_flight for space in self.spaces) > 0
        ):
            if not self._rtt_initialized:
                timeout = 2 * K_INITIAL_RTT * (2 ** self._pto_count)
            else:
                timeout = (
                    self._rtt_smoothed
                    + max(4 * self._rtt_variance, K_GRANULARITY)
                    + self.max_ack_delay
                ) * (2 ** self._pto_count)
            return self._time_of_last_sent_ack_eliciting_packet + timeout

        return None

    def get_probe_timeout(self) -> float:
        return (
            self._rtt_smoothed
            + max(4 * self._rtt_variance, K_GRANULARITY)
            + self.max_ack_delay
        )

    def on_ack_received(
        self,
        space: QuicPacketSpace,
        ack_rangeset: RangeSet,
        ack_delay: float,
        now: float,
    ) -> None:
        """
        Update metrics as the result of an ACK being received.
        """
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
            latest_rtt = now - largest_sent_time
            log_rtt = True

            # limit ACK delay to max_ack_delay
            ack_delay = min(ack_delay, self.max_ack_delay)

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

            # check whether we should exist slow start
            if self._ssthresh is None and self._rtt_monitor.is_rtt_increasing(
                latest_rtt, now
            ):
                self._ssthresh = self.congestion_window

        else:
            log_rtt = False

        self.detect_loss(space, now=now)

        if self._quic_logger is not None:
            self._log_metrics_updated(log_rtt=log_rtt)

        self._pto_count = 0

    def on_loss_detection_timeout(self, now: float) -> None:
        loss_space = self.get_earliest_loss_time()
        if loss_space is not None:
            self.detect_loss(loss_space, now=now)
        else:
            self._pto_count += 1

            # reschedule some data
            for space in self.spaces:
                for packet_number, packet in list(
                    filter(lambda i: i[1].is_crypto_packet, space.sent_packets.items())
                ):
                    # remove packet and update counters
                    self.on_packet_lost(packet, space)

            self._send_probe()

    def on_packet_acked(self, packet: QuicSentPacket) -> None:
        self.bytes_in_flight -= packet.sent_bytes

        # don't increase window in congestion recovery
        if packet.sent_time <= self._congestion_recovery_start_time:
            return

        if self._ssthresh is None or self.congestion_window < self._ssthresh:
            # slow start
            self.congestion_window += packet.sent_bytes
        else:
            # congestion avoidance
            self._congestion_stash += packet.sent_bytes
            count = self._congestion_stash // self.congestion_window
            if count:
                self._congestion_stash -= count * self.congestion_window
                self.congestion_window += count * K_MAX_DATAGRAM_SIZE

    def on_packet_expired(self, packet: QuicSentPacket) -> None:
        self.bytes_in_flight -= packet.sent_bytes

        if self._quic_logger is not None:
            self._log_metrics_updated()

    def on_packet_lost(self, packet: QuicSentPacket, space: QuicPacketSpace) -> None:
        del space.sent_packets[packet.packet_number]

        if packet.is_ack_eliciting:
            space.ack_eliciting_in_flight -= 1
        if packet.in_flight:
            self.bytes_in_flight -= packet.sent_bytes

        if self._quic_logger is not None:
            self._quic_logger.log_event(
                category="recovery",
                event="packet_lost",
                data={
                    "type": self._quic_logger.packet_type(packet.packet_type),
                    "packet_number": str(packet.packet_number),
                },
            )
            self._log_metrics_updated()

        # trigger callbacks
        for handler, args in packet.delivery_handlers:
            handler(QuicDeliveryState.LOST, *args)

    def on_packet_sent(self, packet: QuicSentPacket, space: QuicPacketSpace) -> None:
        space.sent_packets[packet.packet_number] = packet

        if packet.is_ack_eliciting:
            space.ack_eliciting_in_flight += 1
        if packet.in_flight:
            if packet.is_ack_eliciting:
                self._time_of_last_sent_ack_eliciting_packet = packet.sent_time

            # add packet to bytes in flight
            self.bytes_in_flight += packet.sent_bytes

            if self._quic_logger is not None:
                self._log_metrics_updated()

    def on_packets_lost(self, lost_largest_time: float, now: float) -> None:
        # start a new congestion event if packet was sent after the
        # start of the previous congestion recovery period.
        if lost_largest_time > self._congestion_recovery_start_time:
            self._congestion_recovery_start_time = now
            self.congestion_window = max(
                int(self.congestion_window * K_LOSS_REDUCTION_FACTOR), K_MINIMUM_WINDOW
            )
            self._ssthresh = self.congestion_window

            if self._quic_logger is not None:
                self._log_metrics_updated()

        # TODO : collapse congestion window if persistent congestion

    def _log_metrics_updated(self, log_rtt=False) -> None:
        data = {"bytes_in_flight": self.bytes_in_flight, "cwnd": self.congestion_window}
        if self._ssthresh is not None:
            data["ssthresh"] = self._ssthresh

        if log_rtt:
            data.update(
                {
                    "latest_rtt": int(self._rtt_latest * 1000),
                    "min_rtt": int(self._rtt_min * 1000),
                    "smoothed_rtt": int(self._rtt_smoothed * 1000),
                    "rtt_variance": int(self._rtt_variance * 1000),
                }
            )

        self._quic_logger.log_event(
            category="recovery", event="metrics_updated", data=data
        )


class QuicRttMonitor:
    """
    Roundtrip time monitor for HyStart.
    """

    def __init__(self) -> None:
        self._increases = 0
        self._last_time = None
        self._ready = False
        self._size = 5

        self._filtered_min: Optional[float] = None

        self._sample_idx = 0
        self._sample_max: Optional[float] = None
        self._sample_min: Optional[float] = None
        self._sample_time = 0.0
        self._samples = [0.0 for i in range(self._size)]

    def add_rtt(self, rtt: float) -> None:
        self._samples[self._sample_idx] = rtt
        self._sample_idx += 1

        if self._sample_idx >= self._size:
            self._sample_idx = 0
            self._ready = True

        if self._ready:
            self._sample_max = self._samples[0]
            self._sample_min = self._samples[0]
            for sample in self._samples[1:]:
                if sample < self._sample_min:
                    self._sample_min = sample
                elif sample > self._sample_max:
                    self._sample_max = sample

    def is_rtt_increasing(self, rtt: float, now: float) -> bool:
        if now > self._sample_time + K_GRANULARITY:
            self.add_rtt(rtt)
            self._sample_time = now

            if self._ready:
                if self._filtered_min is None or self._filtered_min > self._sample_max:
                    self._filtered_min = self._sample_max

                delta = self._sample_min - self._filtered_min
                if delta * 4 >= self._filtered_min:
                    self._increases += 1
                    if self._increases >= self._size:
                        return True
                elif delta > 0:
                    self._increases = 0
        return False
