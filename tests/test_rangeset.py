from unittest import TestCase

from aioquic.rangeset import RangeSet


class RangeSetTest(TestCase):
    def test_add_duplicate(self):
        rangeset = RangeSet()

        rangeset.add(0)
        self.assertEqual(rangeset.ranges, [
            range(0, 1),
        ])

        rangeset.add(0)
        self.assertEqual(rangeset.ranges, [
            range(0, 1),
        ])

    def test_add_ordered(self):
        rangeset = RangeSet()

        rangeset.add(0)
        self.assertEqual(rangeset.ranges, [
            range(0, 1),
        ])

        rangeset.add(1)
        self.assertEqual(rangeset.ranges, [
            range(0, 2),
        ])

        rangeset.add(2)
        self.assertEqual(rangeset.ranges, [
            range(0, 3),
        ])

    def test_add_merge(self):
        rangeset = RangeSet()

        rangeset.add(0)
        self.assertEqual(rangeset.ranges, [
            range(0, 1),
        ])

        rangeset.add(2)
        self.assertEqual(rangeset.ranges, [
            range(0, 1),
            range(2, 3),
        ])

        rangeset.add(1)
        self.assertEqual(rangeset.ranges, [
            range(0, 3),
        ])

    def test_add_reverse(self):
        rangeset = RangeSet()

        rangeset.add(2)
        self.assertEqual(rangeset.ranges, [
            range(2, 3),
        ])

        rangeset.add(1)
        self.assertEqual(rangeset.ranges, [
            range(1, 3),
        ])

        rangeset.add(0)
        self.assertEqual(rangeset.ranges, [
            range(0, 3),
        ])

    def test_bool(self):
        rangeset = RangeSet()
        self.assertFalse(bool(rangeset))

        rangeset.add(0)
        self.assertTrue(bool(rangeset))
