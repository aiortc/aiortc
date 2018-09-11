from unittest import TestCase

from aiortc.rate import RateBucket, RateCounter


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
