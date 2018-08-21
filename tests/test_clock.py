import datetime
from unittest import TestCase

from aiortc.clock import datetime_from_ntp, datetime_to_ntp


class ClockTest(TestCase):
    def test_datetime_from_ntp(self):
        dt = datetime.datetime(2018, 6, 28, 9, 3, 5, 423998, tzinfo=datetime.timezone.utc)
        self.assertEqual(datetime_from_ntp(16059593044731306503), dt)

    def test_datetime_to_ntp(self):
        dt = datetime.datetime(2018, 6, 28, 9, 3, 5, 423998, tzinfo=datetime.timezone.utc)
        self.assertEqual(datetime_to_ntp(dt), 16059593044731306503)
