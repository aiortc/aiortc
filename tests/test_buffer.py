from unittest import TestCase

from aioquic.buffer import (
    Buffer,
    BufferReadError,
    BufferWriteError,
    pull_bytes,
    pull_uint8,
    pull_uint16,
    pull_uint32,
    pull_uint64,
    push_bytes,
    push_uint8,
    push_uint16,
    push_uint32,
    push_uint64,
)


class BufferTest(TestCase):
    def test_pull_bytes(self):
        buf = Buffer(data=b"\x08\x07\x06\x05\x04\x03\x02\x01")
        self.assertEqual(pull_bytes(buf, 3), b"\x08\x07\x06")

    def test_pull_bytes_truncated(self):
        buf = Buffer(capacity=0)
        with self.assertRaises(BufferReadError):
            pull_bytes(buf, 2)
        self.assertEqual(buf.tell(), 0)

    def test_pull_uint8(self):
        buf = Buffer(data=b"\x08\x07\x06\x05\x04\x03\x02\x01")
        self.assertEqual(pull_uint8(buf), 0x08)
        self.assertEqual(buf.tell(), 1)

    def test_pull_uint8_truncated(self):
        buf = Buffer(capacity=0)
        with self.assertRaises(BufferReadError):
            pull_uint8(buf)
        self.assertEqual(buf.tell(), 0)

    def test_pull_uint16(self):
        buf = Buffer(data=b"\x08\x07\x06\x05\x04\x03\x02\x01")
        self.assertEqual(pull_uint16(buf), 0x0807)
        self.assertEqual(buf.tell(), 2)

    def test_pull_uint16_truncated(self):
        buf = Buffer(capacity=1)
        with self.assertRaises(BufferReadError):
            pull_uint16(buf)
        self.assertEqual(buf.tell(), 0)

    def test_pull_uint32(self):
        buf = Buffer(data=b"\x08\x07\x06\x05\x04\x03\x02\x01")
        self.assertEqual(pull_uint32(buf), 0x08070605)
        self.assertEqual(buf.tell(), 4)

    def test_pull_uint32_truncated(self):
        buf = Buffer(capacity=3)
        with self.assertRaises(BufferReadError):
            pull_uint32(buf)
        self.assertEqual(buf.tell(), 0)

    def test_pull_uint64(self):
        buf = Buffer(data=b"\x08\x07\x06\x05\x04\x03\x02\x01")
        self.assertEqual(pull_uint64(buf), 0x0807060504030201)
        self.assertEqual(buf.tell(), 8)

    def test_pull_uint64_truncated(self):
        buf = Buffer(capacity=7)
        with self.assertRaises(BufferReadError):
            pull_uint64(buf)
        self.assertEqual(buf.tell(), 0)

    def test_push_bytes(self):
        buf = Buffer(capacity=3)
        push_bytes(buf, b"\x08\x07\x06")
        self.assertEqual(buf.data, b"\x08\x07\x06")
        self.assertEqual(buf.tell(), 3)

    def test_push_bytes_truncated(self):
        buf = Buffer(capacity=3)
        with self.assertRaises(BufferWriteError):
            push_bytes(buf, b"\x08\x07\x06\x05")
        self.assertEqual(buf.tell(), 0)

    def test_push_uint8(self):
        buf = Buffer(capacity=1)
        push_uint8(buf, 0x08)
        self.assertEqual(buf.data, b"\x08")
        self.assertEqual(buf.tell(), 1)

    def test_push_uint16(self):
        buf = Buffer(capacity=2)
        push_uint16(buf, 0x0807)
        self.assertEqual(buf.data, b"\x08\x07")
        self.assertEqual(buf.tell(), 2)

    def test_push_uint32(self):
        buf = Buffer(capacity=4)
        push_uint32(buf, 0x08070605)
        self.assertEqual(buf.data, b"\x08\x07\x06\x05")
        self.assertEqual(buf.tell(), 4)

    def test_push_uint64(self):
        buf = Buffer(capacity=8)
        push_uint64(buf, 0x0807060504030201)
        self.assertEqual(buf.data, b"\x08\x07\x06\x05\x04\x03\x02\x01")
        self.assertEqual(buf.tell(), 8)

    def test_seek(self):
        buf = Buffer(data=b"01234567")
        self.assertFalse(buf.eof())
        self.assertEqual(buf.tell(), 0)

        buf.seek(4)
        self.assertFalse(buf.eof())
        self.assertEqual(buf.tell(), 4)

        buf.seek(8)
        self.assertTrue(buf.eof())
        self.assertEqual(buf.tell(), 8)
