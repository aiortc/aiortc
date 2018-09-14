from aiortc.utils import uint32_add, uint32_gt

BURST_DELTA_THRESHOLD_MS = 5


class TimestampGroup:
    def __init__(self, timestamp=None):
        self.arrival_time = None
        self.first_timestamp = timestamp
        self.last_timestamp = timestamp
        self.size = 0

    def __repr__(self):
        return 'TimestampGroup(arrival_time=%s, timestamp=%s, size=%s)' % (
            self.arrival_time,
            self.last_timestamp,
            self.size)


class InterArrival:
    """
    Inter-arrival time and size filter.

    Adapted from the webrtc.org codebase.
    """
    def __init__(self, group_length, timestamp_to_ms):
        self.group_length = group_length
        self.timestamp_to_ms = timestamp_to_ms
        self.current_group = None
        self.previous_group = None

    def compute_deltas(self, timestamp, arrival_time, packet_size):
        deltas = None
        if self.current_group is None:
            self.current_group = TimestampGroup(timestamp)
        elif self.packet_out_of_order(timestamp):
            return deltas
        elif self.new_timestamp_group(timestamp, arrival_time):
            if self.previous_group is not None:
                timestamp_delta = uint32_add(self.current_group.last_timestamp,
                                             -self.previous_group.last_timestamp)
                arrival_time_delta = (self.current_group.arrival_time -
                                      self.previous_group.arrival_time)
                packet_size_delta = self.current_group.size - self.previous_group.size
                deltas = (timestamp_delta, arrival_time_delta, packet_size_delta)

            # shift groups
            self.previous_group = self.current_group
            self.current_group = TimestampGroup(timestamp=timestamp)
        elif uint32_gt(timestamp, self.current_group.last_timestamp):
            self.current_group.last_timestamp = timestamp

        self.current_group.size += packet_size
        self.current_group.arrival_time = arrival_time

        return deltas

    def belongs_to_burst(self, timestamp, arrival_time):
        timestamp_delta = uint32_add(timestamp, -self.current_group.last_timestamp)
        timestamp_delta_ms = round(self.timestamp_to_ms * timestamp_delta)
        arrival_time_delta = arrival_time - self.current_group.arrival_time
        return (timestamp_delta_ms == 0 or
                ((arrival_time_delta - timestamp_delta_ms) < 0 and
                 arrival_time_delta <= BURST_DELTA_THRESHOLD_MS))

    def new_timestamp_group(self, timestamp, arrival_time):
        if self.belongs_to_burst(timestamp, arrival_time):
            return False
        else:
            timestamp_delta = uint32_add(timestamp, -self.current_group.first_timestamp)
            return timestamp_delta > self.group_length

    def packet_out_of_order(self, timestamp):
        timestamp_delta = uint32_add(timestamp, -self.current_group.first_timestamp)
        return timestamp_delta >= 0x80000000


class RateBucket:
    def __init__(self, count=0, value=0):
        self.count = count
        self.value = value

    def __eq__(self, other):
        return self.count == other.count and self.value == other.value


class RateCounter:
    """
    Rate counter, which stores the amount received in 1ms buckets.
    """
    def __init__(self, window_size, scale=8000):
        self._scale = scale
        self._window_size = window_size
        self.reset()

    def add(self, value, now_ms):
        if self._origin_ms is None:
            self._origin_ms = now_ms
        else:
            self._erase_old(now_ms)

        index = (self._origin_index + now_ms - self._origin_ms) % self._window_size
        self._buckets[index].count += 1
        self._buckets[index].value += value
        self._total.count += 1
        self._total.value += value

    def rate(self, now_ms):
        if self._origin_ms is not None:
            self._erase_old(now_ms)
            active_window_size = now_ms - self._origin_ms + 1
            if self._total.count > 0 and active_window_size > 1:
                return round(self._scale * self._total.value / active_window_size)

    def reset(self):
        self._buckets = [RateBucket() for i in range(self._window_size)]
        self._origin_index = 0
        self._origin_ms = None
        self._total = RateBucket()

    def _erase_old(self, now_ms):
        new_origin_ms = now_ms - self._window_size + 1
        while self._origin_ms < new_origin_ms:
            bucket = self._buckets[self._origin_index]
            self._total.count -= bucket.count
            self._total.value -= bucket.value
            bucket.count = 0
            bucket.value = 0

            self._origin_index = (self._origin_index + 1) % self._window_size
            self._origin_ms += 1
