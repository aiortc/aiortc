import struct
from struct import pack_into, unpack_from


class BufferReadError(ValueError):
    pass


class Buffer:
    def __init__(self, capacity=None, data=None):
        if data is not None:
            self._data = data
            self._length = len(data)
        else:
            self._data = bytearray(capacity)
            self._length = capacity
        self._pos = 0

    @property
    def capacity(self):
        return self._length

    @property
    def data(self):
        return bytes(self._data[: self._pos])

    def data_slice(self, start, end):
        return bytes(self._data[start:end])

    def eof(self):
        return self._pos == self._length

    def seek(self, pos):
        assert pos <= self._length
        self._pos = pos

    def tell(self):
        return self._pos


# BYTES


def pull_bytes(buf: Buffer, length: int) -> bytes:
    """
    Pull bytes.
    """
    if buf._pos + length > buf._length:
        raise BufferReadError
    v = buf._data[buf._pos : buf._pos + length]
    buf._pos += length
    return v


def push_bytes(buf: Buffer, v: bytes):
    """
    Push bytes.
    """
    length = len(v)
    buf._data[buf._pos : buf._pos + length] = v
    buf._pos += length


# INTEGERS


def pull_uint8(buf: Buffer) -> int:
    """
    Pull an 8-bit unsigned integer.
    """
    try:
        v = buf._data[buf._pos]
        buf._pos += 1
        return v
    except IndexError:
        raise BufferReadError


def push_uint8(buf: Buffer, v: int):
    """
    Push an 8-bit unsigned integer.
    """
    buf._data[buf._pos] = v
    buf._pos += 1


def pull_uint16(buf: Buffer) -> int:
    """
    Pull a 16-bit unsigned integer.
    """
    try:
        v, = struct.unpack_from("!H", buf._data, buf._pos)
        buf._pos += 2
        return v
    except struct.error:
        raise BufferReadError


def push_uint16(buf: Buffer, v: int):
    """
    Push a 16-bit unsigned integer.
    """
    pack_into("!H", buf._data, buf._pos, v)
    buf._pos += 2


def pull_uint32(buf: Buffer) -> int:
    """
    Pull a 32-bit unsigned integer.
    """
    try:
        v, = struct.unpack_from("!L", buf._data, buf._pos)
        buf._pos += 4
        return v
    except struct.error:
        raise BufferReadError


def push_uint32(buf: Buffer, v: int):
    """
    Push a 32-bit unsigned integer.
    """
    pack_into("!L", buf._data, buf._pos, v)
    buf._pos += 4


def pull_uint64(buf: Buffer) -> int:
    """
    Pull a 64-bit unsigned integer.
    """
    try:
        v, = unpack_from("!Q", buf._data, buf._pos)
        buf._pos += 8
        return v
    except struct.error:
        raise BufferReadError


def push_uint64(buf: Buffer, v: int):
    """
    Push a 64-bit unsigned integer.
    """
    pack_into("!Q", buf._data, buf._pos, v)
    buf._pos += 8
