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
