from unittest import TestCase

from numpy import random

from aiortc.rate import (AimdRateControl, BandwidthUsage, InterArrival,
                         OveruseDetector, OveruseEstimator, RateBucket,
                         RateControlState, RateCounter)

TIMESTAMP_GROUP_LENGTH_US = 5000
MIN_STEP_US = 20
TRIGGER_NEW_GROUP_US = TIMESTAMP_GROUP_LENGTH_US + MIN_STEP_US
BURST_THRESHOLD_MS = 5

START_RTP_TIMESTAMP_WRAP_US = 47721858827
START_ABS_SEND_TIME_WRAP_US = 63999995


def abs_send_time(us):
    absolute_send_time = (((us << 18) + 500000) // 1000000) & 0xFFFFFF
    return absolute_send_time << 8


def rtp_timestamp(us):
    return ((us * 90 + 500) // 1000) & 0xFFFFFFFF


class AimdRateControlTest(TestCase):
    def setUp(self):
        self.rate_control = AimdRateControl()

    def test_update_normal(self):
        bitrate = 300000
        now_ms = 0
        self.rate_control.set_estimate(bitrate, now_ms)
        estimate = self.rate_control.update(BandwidthUsage.NORMAL, bitrate, now_ms)
        self.assertEqual(estimate, 301000)

        self.assertEqual(self.rate_control.state, RateControlState.INCREASE)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, None)
        self.assertEqual(self.rate_control.var_max_bitrate_kbps, 0.4)

    def test_update_normal_no_estimated_throughput(self):
        bitrate = 300000
        now_ms = 0
        self.rate_control.set_estimate(bitrate, now_ms)
        estimate = self.rate_control.update(BandwidthUsage.NORMAL, None, now_ms)
        self.assertEqual(estimate, 301000)

    def test_update_overuse(self):
        bitrate = 300000
        now_ms = 0
        self.rate_control.set_estimate(bitrate, now_ms)
        estimate = self.rate_control.update(BandwidthUsage.OVERUSING, bitrate, now_ms)
        self.assertEqual(estimate, 255000)

        self.assertEqual(self.rate_control.state, RateControlState.HOLD)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, 300.0)
        self.assertEqual(self.rate_control.var_max_bitrate_kbps, 0.4)

    def test_update_underuse(self):
        bitrate = 300000
        now_ms = 0
        self.rate_control.set_estimate(bitrate, now_ms)
        estimate = self.rate_control.update(BandwidthUsage.UNDERUSING, bitrate, now_ms)
        self.assertEqual(estimate, 300000)

        self.assertEqual(self.rate_control.state, RateControlState.HOLD)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, None)
        self.assertEqual(self.rate_control.var_max_bitrate_kbps, 0.4)

    def test_additive_rate_increase(self):
        acked_bitrate = 100000
        self.rate_control.set_estimate(acked_bitrate, 0)
        for now_ms in range(0, 20000, 100):
            estimate = self.rate_control.update(BandwidthUsage.NORMAL, acked_bitrate, now_ms)
        self.assertEqual(estimate, 160000)
        self.assertEqual(self.rate_control.near_max, False)

        # overuse -> hold
        estimate = self.rate_control.update(BandwidthUsage.OVERUSING, acked_bitrate, now_ms)
        self.assertEqual(estimate, 85000)
        self.assertEqual(self.rate_control.near_max, True)
        now_ms += 1000

        # back to normal -> hold
        estimate = self.rate_control.update(BandwidthUsage.NORMAL, acked_bitrate, now_ms)
        self.assertEqual(estimate, 85000)
        self.assertEqual(self.rate_control.near_max, True)
        now_ms += 1000

        # still normal -> additive increase
        estimate = self.rate_control.update(BandwidthUsage.NORMAL, acked_bitrate, now_ms)
        self.assertEqual(estimate, 94444)
        self.assertEqual(self.rate_control.near_max, True)
        now_ms += 1000

        # overuse -> hold
        estimate = self.rate_control.update(BandwidthUsage.OVERUSING, acked_bitrate, now_ms)
        self.assertEqual(estimate, 85000)
        self.assertEqual(self.rate_control.near_max, True)
        now_ms += 1000

    def test_clear_max_throughput(self):
        normal_bitrate = 100000
        high_bitrate = 150000
        now_ms = 0
        self.rate_control.set_estimate(normal_bitrate, now_ms)
        self.rate_control.update(BandwidthUsage.NORMAL, normal_bitrate, now_ms)
        now_ms += 1000

        # overuse
        self.rate_control.update(BandwidthUsage.OVERUSING, normal_bitrate, now_ms)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, 100.0)
        now_ms += 1000

        # stable
        self.rate_control.update(BandwidthUsage.NORMAL, normal_bitrate, now_ms)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, 100.0)
        now_ms += 1000

        # large increase in throughput
        self.rate_control.update(BandwidthUsage.NORMAL, high_bitrate, now_ms)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, None)
        now_ms += 1000

        # overuse
        self.rate_control.update(BandwidthUsage.OVERUSING, high_bitrate, now_ms)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, 150.0)
        now_ms += 1000

        # overuse and large decrease in throughput
        self.rate_control.update(BandwidthUsage.OVERUSING, normal_bitrate, now_ms)
        self.assertEqual(self.rate_control.avg_max_bitrate_kbps, 100.0)
        now_ms += 1000

    def test_bwe_limited_by_acked_bitrate(self):
        acked_bitrate = 10000
        self.rate_control.set_estimate(acked_bitrate, 0)
        for now_ms in range(0, 20000, 100):
            estimate = self.rate_control.update(BandwidthUsage.NORMAL, acked_bitrate, now_ms)
        self.assertEqual(estimate, 25000)

    def test_bwe_not_limited_by_decreasing_acked_bitrate(self):
        acked_bitrate = 100000
        self.rate_control.set_estimate(acked_bitrate, 0)
        for now_ms in range(0, 20000, 100):
            estimate = self.rate_control.update(BandwidthUsage.NORMAL, acked_bitrate, now_ms)
        self.assertEqual(estimate, 160000)

        # estimate doesn't change
        estimate = self.rate_control.update(BandwidthUsage.NORMAL, acked_bitrate // 2, now_ms)
        self.assertEqual(estimate, 160000)


class InterArrivalTest(TestCase):
    def setUp(self):
        self.inter_arrival_ast = InterArrival(
            abs_send_time(TIMESTAMP_GROUP_LENGTH_US), 1000 / (1 << 26))
        self.inter_arrival_rtp = InterArrival(
            rtp_timestamp(TIMESTAMP_GROUP_LENGTH_US), 1 / 9)

    def assertComputed(self, timestamp_us, arrival_time_ms, packet_size,
                       timestamp_delta_us, arrival_time_delta_ms, packet_size_delta,
                       timestamp_near=0):
        # AbsSendTime
        deltas = self.inter_arrival_ast.compute_deltas(abs_send_time(timestamp_us),
                                                       arrival_time_ms, packet_size)
        self.assertIsNotNone(deltas)
        self.assertAlmostEqual(deltas.timestamp, abs_send_time(timestamp_delta_us),
                               delta=timestamp_near << 8)
        self.assertEqual(deltas.arrival_time, arrival_time_delta_ms)
        self.assertEqual(deltas.size, packet_size_delta)

        # RtpTimestamp
        deltas = self.inter_arrival_rtp.compute_deltas(rtp_timestamp(timestamp_us),
                                                       arrival_time_ms, packet_size)
        self.assertIsNotNone(deltas)
        self.assertAlmostEqual(deltas.timestamp, rtp_timestamp(timestamp_delta_us),
                               delta=timestamp_near)
        self.assertEqual(deltas.arrival_time, arrival_time_delta_ms)
        self.assertEqual(deltas.size, packet_size_delta)

    def assertNotComputed(self, timestamp_us, arrival_time_ms, packet_size):
        self.assertIsNone(self.inter_arrival_ast.compute_deltas(
            abs_send_time(timestamp_us), arrival_time_ms, packet_size))
        self.assertIsNone(self.inter_arrival_rtp.compute_deltas(
            rtp_timestamp(timestamp_us), arrival_time_ms, packet_size))

    def wrapTest(self, wrap_start_us, unorderly_within_group):
        timestamp_near = 1

        # G1
        arrival_time = 17
        self.assertNotComputed(0, arrival_time, 1)

        # G2
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertNotComputed(wrap_start_us // 4, arrival_time, 1)

        # G3
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(wrap_start_us // 2, arrival_time, 1,
                            wrap_start_us // 4, 6, 0)

        # G4
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(wrap_start_us // 2 + wrap_start_us // 4, arrival_time, 1,
                            wrap_start_us // 4, 6, 0, timestamp_near)
        g4_arrival_time = arrival_time

        # G5
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(wrap_start_us, arrival_time, 2,
                            wrap_start_us // 4, 6, 0, timestamp_near)
        for i in range(10):
            arrival_time += BURST_THRESHOLD_MS + 1
            if unorderly_within_group:
                self.assertNotComputed(wrap_start_us + (9 - i) * MIN_STEP_US, arrival_time, 1)
            else:
                self.assertNotComputed(wrap_start_us + i * MIN_STEP_US, arrival_time, 1)
        g5_arrival_time = arrival_time

        # out of order
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertNotComputed(wrap_start_us - 100, arrival_time, 100)

        # G6
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(wrap_start_us + TRIGGER_NEW_GROUP_US, arrival_time, 10,
                            wrap_start_us // 4 + 9 * MIN_STEP_US,
                            g5_arrival_time - g4_arrival_time,
                            11, timestamp_near)
        g6_arrival_time = arrival_time

        # out of order
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertNotComputed(wrap_start_us + TIMESTAMP_GROUP_LENGTH_US, arrival_time, 100)

        # G7
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(wrap_start_us + 2 * TRIGGER_NEW_GROUP_US, arrival_time, 10,
                            TRIGGER_NEW_GROUP_US - 9 * MIN_STEP_US,
                            g6_arrival_time - g5_arrival_time,
                            -2, timestamp_near)

    def test_first_packet(self):
        self.assertNotComputed(0, 17, 1)

    def test_first_group(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertNotComputed(timestamp, arrival_time, 2)
        g2_arrival_time = arrival_time

        # G3
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(timestamp, arrival_time, 1,
                            TRIGGER_NEW_GROUP_US, g2_arrival_time - g1_arrival_time, 1)

    def test_second_group(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertNotComputed(timestamp, arrival_time, 2)
        g2_arrival_time = arrival_time

        # G3
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(timestamp, arrival_time, 1,
                            TRIGGER_NEW_GROUP_US, g2_arrival_time - g1_arrival_time, 1)
        g3_arrival_time = arrival_time

        # G4
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(timestamp, arrival_time, 2,
                            TRIGGER_NEW_GROUP_US, g3_arrival_time - g2_arrival_time, -1)

    def test_accumulated_group(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_timestamp = timestamp
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertNotComputed(timestamp, 28, 2)
        for i in range(10):
            timestamp += MIN_STEP_US
            arrival_time += BURST_THRESHOLD_MS + 1
            self.assertNotComputed(timestamp, arrival_time, 1)
        g2_timestamp = timestamp
        g2_arrival_time = arrival_time

        # G3
        timestamp = 2 * TRIGGER_NEW_GROUP_US
        arrival_time = 500
        self.assertComputed(timestamp, arrival_time, 100,
                            g2_timestamp - g1_timestamp, g2_arrival_time - g1_arrival_time, 11)

    def test_out_of_order_packet(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_timestamp = timestamp
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += 11
        self.assertNotComputed(timestamp, 28, 2)
        for i in range(10):
            timestamp += MIN_STEP_US
            arrival_time += BURST_THRESHOLD_MS + 1
            self.assertNotComputed(timestamp, arrival_time, 1)
        g2_timestamp = timestamp
        g2_arrival_time = arrival_time

        # out of order packet
        arrival_time = 281
        self.assertNotComputed(g1_timestamp, arrival_time, 1)

        # G3
        timestamp = 2 * TRIGGER_NEW_GROUP_US
        arrival_time = 500
        self.assertComputed(timestamp, arrival_time, 100,
                            g2_timestamp - g1_timestamp, g2_arrival_time - g1_arrival_time, 11)

    def test_out_of_order_within_group(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_timestamp = timestamp
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time += 11
        self.assertNotComputed(timestamp, 28, 2)
        timestamp += 10 * MIN_STEP_US
        g2_timestamp = timestamp
        for i in range(10):
            arrival_time += BURST_THRESHOLD_MS + 1
            self.assertNotComputed(timestamp, arrival_time, 1)
            timestamp -= MIN_STEP_US
        g2_arrival_time = arrival_time

        # out of order packet
        arrival_time = 281
        self.assertNotComputed(g1_timestamp, arrival_time, 1)

        # G3
        timestamp = 2 * TRIGGER_NEW_GROUP_US
        arrival_time = 500
        self.assertComputed(timestamp, arrival_time, 100,
                            g2_timestamp - g1_timestamp, g2_arrival_time - g1_arrival_time, 11)

    def test_two_bursts(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_timestamp = timestamp
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time = 100
        for i in range(10):
            timestamp += 30000
            arrival_time += BURST_THRESHOLD_MS
            self.assertNotComputed(timestamp, arrival_time, 1)
        g2_timestamp = timestamp
        g2_arrival_time = arrival_time

        # G3
        timestamp += 30000
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(timestamp, arrival_time, 100,
                            g2_timestamp - g1_timestamp, g2_arrival_time - g1_arrival_time, 9)

    def test_no_bursts(self):
        # G1
        timestamp = 0
        arrival_time = 17
        self.assertNotComputed(timestamp, arrival_time, 1)
        g1_timestamp = timestamp
        g1_arrival_time = arrival_time

        # G2
        timestamp += TRIGGER_NEW_GROUP_US
        arrival_time = 28
        self.assertNotComputed(timestamp, arrival_time, 2)
        g2_timestamp = timestamp
        g2_arrival_time = arrival_time

        # G3
        timestamp += 30000
        arrival_time += BURST_THRESHOLD_MS + 1
        self.assertComputed(timestamp, arrival_time, 100,
                            g2_timestamp - g1_timestamp, g2_arrival_time - g1_arrival_time, 1)

    def test_wrap_abs_send_time(self):
        self.wrapTest(START_ABS_SEND_TIME_WRAP_US, False)

    def test_wrap_abs_send_time_out_of_order_within_group(self):
        self.wrapTest(START_ABS_SEND_TIME_WRAP_US, True)

    def test_wrap_rtp_timestamp(self):
        self.wrapTest(START_RTP_TIMESTAMP_WRAP_US, False)

    def test_wrap_rtp_timestamp_out_of_order_within_group(self):
        self.wrapTest(START_RTP_TIMESTAMP_WRAP_US, True)


class OveruseDetectorTest(TestCase):
    def setUp(self):
        self.timestamp_to_ms = 1 / 90
        self.detector = OveruseDetector()
        self.estimator = OveruseEstimator()
        self.inter_arrival = InterArrival(5 * 90, 1 / 9)

        self.packet_size = 1200
        self.now_ms = 0
        self.receive_time_ms = 0
        self.rtp_timestamp = 900

        random.seed(21)

    def test_simple_non_overuse_30fps(self):
        frame_duration_ms = 33

        for i in range(1000):
            self.update_detector(self.rtp_timestamp, self.now_ms)
            self.now_ms += frame_duration_ms
            self.rtp_timestamp += frame_duration_ms * 90
        self.assertEqual(self.detector.state(), BandwidthUsage.NORMAL)

    def test_simple_non_overuse_with_receive_variance(self):
        frame_duration_ms = 10

        for i in range(1000):
            self.update_detector(self.rtp_timestamp, self.now_ms)
            self.rtp_timestamp += frame_duration_ms * 90
            if i % 2:
                self.now_ms += frame_duration_ms - 5
            else:
                self.now_ms += frame_duration_ms + 5
            self.assertEqual(self.detector.state(), BandwidthUsage.NORMAL)

    def test_simple_non_overuse_with_rtp_timestamp_variance(self):
        frame_duration_ms = 10

        for i in range(1000):
            self.update_detector(self.rtp_timestamp, self.now_ms)
            self.now_ms += frame_duration_ms
            if i % 2:
                self.rtp_timestamp += (frame_duration_ms - 5) * 90
            else:
                self.rtp_timestamp += (frame_duration_ms + 5) * 90
            self.assertEqual(self.detector.state(), BandwidthUsage.NORMAL)

    def test_simple_overuse_2000Kbit_30fps(self):
        packets_per_frame = 6
        frame_duration_ms = 33
        drift_per_frame_ms = 1
        sigma_ms = 0

        unique_overuse = self.run_100000_samples(packets_per_frame, frame_duration_ms, sigma_ms)
        self.assertEqual(unique_overuse, 0)

        frames_until_overuse = self.run_until_overuse(packets_per_frame, frame_duration_ms,
                                                      sigma_ms, drift_per_frame_ms)
        self.assertEqual(frames_until_overuse, 7)

    def test_simple_overuse_100Kbit_10fps(self):
        packets_per_frame = 1
        frame_duration_ms = 100
        drift_per_frame_ms = 1
        sigma_ms = 0

        unique_overuse = self.run_100000_samples(packets_per_frame, frame_duration_ms, sigma_ms)
        self.assertEqual(unique_overuse, 0)

        frames_until_overuse = self.run_until_overuse(packets_per_frame, frame_duration_ms,
                                                      sigma_ms, drift_per_frame_ms)
        self.assertEqual(frames_until_overuse, 7)

    def test_overuse_with_low_variance_2000Kbit_30fps(self):
        frame_duration_ms = 33
        drift_per_frame_ms = 1
        self.rtp_timestamp = frame_duration_ms * 90
        offset = 0

        # run 1000 samples to reach steady state
        for i in range(1000):
            for j in range(6):
                self.update_detector(self.rtp_timestamp, self.now_ms)
            self.rtp_timestamp += frame_duration_ms * 90
            if i % 2:
                offset = random.randint(0, 1)
                self.now_ms += frame_duration_ms - offset
            else:
                self.now_ms += frame_duration_ms + offset
            self.assertEqual(self.detector.state(), BandwidthUsage.NORMAL)

        # simulate a higher send pace, that is too high.
        for i in range(3):
            for j in range(6):
                self.update_detector(self.rtp_timestamp, self.now_ms)
            self.now_ms += frame_duration_ms + drift_per_frame_ms * 6
            self.rtp_timestamp += frame_duration_ms * 90
            self.assertEqual(self.detector.state(), BandwidthUsage.NORMAL)

        self.update_detector(self.rtp_timestamp, self.now_ms)
        self.assertEqual(self.detector.state(), BandwidthUsage.OVERUSING)

    def test_low_gaussian_variance_fast_drift_30Kbit_3fps(self):
        packets_per_frame = 1
        frame_duration_ms = 333
        drift_per_frame_ms = 100
        sigma_ms = 3

        unique_overuse = self.run_100000_samples(packets_per_frame, frame_duration_ms, sigma_ms)
        self.assertEqual(unique_overuse, 0)

        frames_until_overuse = self.run_until_overuse(packets_per_frame, frame_duration_ms,
                                                      sigma_ms, drift_per_frame_ms)
        self.assertEqual(frames_until_overuse, 4)

    def test_high_haussian_variance_30Kbit_3fps(self):
        packets_per_frame = 1
        frame_duration_ms = 333
        drift_per_frame_ms = 1
        sigma_ms = 10

        unique_overuse = self.run_100000_samples(packets_per_frame, frame_duration_ms, sigma_ms)
        self.assertEqual(unique_overuse, 0)

        frames_until_overuse = self.run_until_overuse(packets_per_frame, frame_duration_ms,
                                                      sigma_ms, drift_per_frame_ms)
        self.assertEqual(frames_until_overuse, 44)

    def run_100000_samples(self, packets_per_frame, mean_ms, standard_deviation_ms):
        unique_overuse = 0
        last_overuse = -1

        for i in range(100000):
            for j in range(packets_per_frame):
                self.update_detector(self.rtp_timestamp, self.receive_time_ms)
            self.rtp_timestamp += mean_ms * 90
            self.now_ms += mean_ms
            self.receive_time_ms = max(
                self.receive_time_ms,
                int(self.now_ms + random.normal(0, standard_deviation_ms) + 0.5))

            if self.detector.state() == BandwidthUsage.OVERUSING:
                if last_overuse + 1 != i:
                    unique_overuse += 1
                last_overuse = i

        return unique_overuse

    def run_until_overuse(self, packets_per_frame, mean_ms, standard_deviation_ms,
                          drift_per_frame_ms):
        for i in range(100000):
            for j in range(packets_per_frame):
                self.update_detector(self.rtp_timestamp, self.receive_time_ms)
            self.rtp_timestamp += mean_ms * 90
            self.now_ms += mean_ms + drift_per_frame_ms
            self.receive_time_ms = max(
                self.receive_time_ms,
                int(self.now_ms + random.normal(0, standard_deviation_ms) + 0.5))

            if self.detector.state() == BandwidthUsage.OVERUSING:
                return i + 1
        return -1

    def update_detector(self, timestamp, receive_time_ms):
        deltas = self.inter_arrival.compute_deltas(timestamp, receive_time_ms, self.packet_size)
        if deltas is not None:
            timestamp_delta_ms = deltas.timestamp / 90
            self.estimator.update(deltas.arrival_time, timestamp_delta_ms, deltas.size,
                                  self.detector.state(), receive_time_ms)
            self.detector.detect(self.estimator.offset(), timestamp_delta_ms,
                                 self.estimator.num_of_deltas(), receive_time_ms)


class RateCounterTest(TestCase):
    def test_constructor(self):
        counter = RateCounter(10)
        self.assertEqual(counter._buckets, [
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
        ])
        self.assertIsNone(counter._origin_ms)
        self.assertEqual(counter._origin_index, 0)
        self.assertEqual(counter._total, RateBucket())
        self.assertIsNone(counter.rate(0))

    def test_add(self):
        counter = RateCounter(10)

        counter.add(500, 123)
        self.assertEqual(counter._buckets, [
            RateBucket(1, 500),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
        ])
        self.assertEqual(counter._origin_index, 0)
        self.assertEqual(counter._origin_ms, 123)
        self.assertEqual(counter._total, RateBucket(1, 500))
        self.assertIsNone(counter.rate(123))

        counter.add(501, 123)
        self.assertEqual(counter._buckets, [
            RateBucket(2, 1001),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
        ])
        self.assertEqual(counter._origin_index, 0)
        self.assertEqual(counter._origin_ms, 123)
        self.assertEqual(counter._total, RateBucket(2, 1001))
        self.assertIsNone(counter.rate(123))

        counter.add(502, 125)
        self.assertEqual(counter._buckets, [
            RateBucket(2, 1001),
            RateBucket(),
            RateBucket(1, 502),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
        ])
        self.assertEqual(counter._origin_index, 0)
        self.assertEqual(counter._origin_ms, 123)
        self.assertEqual(counter._total, RateBucket(3, 1503))
        self.assertEqual(counter.rate(125), 4008000)

        counter.add(503, 128)
        self.assertEqual(counter._buckets, [
            RateBucket(2, 1001),
            RateBucket(),
            RateBucket(1, 502),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 503),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(),
        ])
        self.assertEqual(counter._origin_index, 0)
        self.assertEqual(counter._origin_ms, 123)
        self.assertEqual(counter._total, RateBucket(4, 2006))
        self.assertEqual(counter.rate(128), 2674667)

        counter.add(504, 132)
        self.assertEqual(counter._buckets, [
            RateBucket(2, 1001),
            RateBucket(),
            RateBucket(1, 502),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 503),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 504),
        ])
        self.assertEqual(counter._origin_index, 0)
        self.assertEqual(counter._origin_ms, 123)
        self.assertEqual(counter._total, RateBucket(5, 2510))
        self.assertEqual(counter.rate(132), 2008000)

        counter.add(505, 134)
        self.assertEqual(counter._buckets, [
            RateBucket(),
            RateBucket(1, 505),
            RateBucket(1, 502),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 503),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 504),
        ])
        self.assertEqual(counter._origin_index, 2)
        self.assertEqual(counter._origin_ms, 125)
        self.assertEqual(counter._total, RateBucket(4, 2014))
        self.assertEqual(counter.rate(134), 1611200)

        counter.add(506, 135)
        self.assertEqual(counter._buckets, [
            RateBucket(),
            RateBucket(1, 505),
            RateBucket(1, 506),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 503),
            RateBucket(),
            RateBucket(),
            RateBucket(),
            RateBucket(1, 504),
        ])
        self.assertEqual(counter._origin_index, 3)
        self.assertEqual(counter._origin_ms, 126)
        self.assertEqual(counter._total, RateBucket(4, 2018))
        self.assertEqual(counter.rate(135), 1614400)
