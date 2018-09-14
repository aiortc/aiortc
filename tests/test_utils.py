from unittest import TestCase

from aiortc.utils import (uint16_add, uint16_gt, uint16_gte, uint32_add,
                          uint32_gt, uint32_gte)


class UtilsTest(TestCase):
    def test_uint16_add(self):
        self.assertEqual(uint16_add(0, 1), 1)
        self.assertEqual(uint16_add(1, 1), 2)
        self.assertEqual(uint16_add(1, 2), 3)
        self.assertEqual(uint16_add(65534, 1), 65535)
        self.assertEqual(uint16_add(65535, 1), 0)
        self.assertEqual(uint16_add(65535, 3), 2)

    def test_uint16_gt(self):
        self.assertFalse(uint16_gt(0, 1))
        self.assertFalse(uint16_gt(1, 1))
        self.assertTrue(uint16_gt(2, 1))
        self.assertTrue(uint16_gt(32768, 1))
        self.assertFalse(uint16_gt(32769, 1))
        self.assertFalse(uint16_gt(65535, 1))

    def test_uint16_gte(self):
        self.assertFalse(uint16_gte(0, 1))
        self.assertTrue(uint16_gte(1, 1))
        self.assertTrue(uint16_gte(2, 1))
        self.assertTrue(uint16_gte(32768, 1))
        self.assertFalse(uint16_gte(32769, 1))
        self.assertFalse(uint16_gte(65535, 1))

    def test_uint32_add(self):
        self.assertEqual(uint32_add(0, 1), 1)
        self.assertEqual(uint32_add(1, 1), 2)
        self.assertEqual(uint32_add(1, 2), 3)
        self.assertEqual(uint32_add(4294967294, 1), 4294967295)
        self.assertEqual(uint32_add(4294967295, 1), 0)
        self.assertEqual(uint32_add(4294967295, 3), 2)

    def test_uint32_gt(self):
        self.assertFalse(uint32_gt(0, 1))
        self.assertFalse(uint32_gt(1, 1))
        self.assertTrue(uint32_gt(2, 1))
        self.assertTrue(uint32_gt(2147483648, 1))
        self.assertFalse(uint32_gt(2147483649, 1))
        self.assertFalse(uint32_gt(4294967295, 1))

    def test_uint32_gte(self):
        self.assertFalse(uint32_gte(0, 1))
        self.assertTrue(uint32_gte(1, 1))
        self.assertTrue(uint32_gte(2, 1))
        self.assertTrue(uint32_gte(2147483648, 1))
        self.assertFalse(uint32_gte(2147483649, 1))
        self.assertFalse(uint32_gte(4294967295, 1))
