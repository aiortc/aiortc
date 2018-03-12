from unittest import TestCase

from aiortc import RTCSessionDescription


class RTCSessionDescriptionTest(TestCase):
    def test_bad_type(self):
        with self.assertRaises(ValueError) as cm:
            RTCSessionDescription(sdp='v=0\r\n', type='bogus')
        self.assertEqual(
            str(cm.exception),
            "'type' must be in ['offer', 'pranswer', 'answer', 'rollback'] (got 'bogus')")

    def test_good_type(self):
        desc = RTCSessionDescription(sdp='v=0\r\n', type='answer')
        self.assertEqual(desc.sdp, 'v=0\r\n')
        self.assertEqual(desc.type, 'answer')
