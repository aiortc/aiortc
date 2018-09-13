import datetime
from unittest import TestCase
from unittest.mock import patch

from aiortc import clock


class ClockTest(TestCase):
    @patch('aiortc.clock.current_datetime')
    def test_current_ms(self, mock_now):
        mock_now.return_value = datetime.datetime(2018, 9, 11, tzinfo=datetime.timezone.utc)
        self.assertEqual(clock.current_ms(), 3745612800000)

        mock_now.return_value = datetime.datetime(
            2018, 9, 11, 0, 0, 1, tzinfo=datetime.timezone.utc)
        self.assertEqual(clock.current_ms(), 3745612801000)

    def test_datetime_from_ntp(self):
        dt = datetime.datetime(2018, 6, 28, 9, 3, 5, 423998, tzinfo=datetime.timezone.utc)
        self.assertEqual(clock.datetime_from_ntp(16059593044731306503), dt)

    def test_datetime_to_ntp(self):
        dt = datetime.datetime(2018, 6, 28, 9, 3, 5, 423998, tzinfo=datetime.timezone.utc)
        self.assertEqual(clock.datetime_to_ntp(dt), 16059593044731306503)
