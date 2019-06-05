import struct
from struct import pack_into, unpack_from
from typing import Optional


class BufferReadError(ValueError):
    pass


class BufferWriteError(ValueError):
    pass


class Buffer:
    def __init__(self, capacity: Optional[int] = 0, data: Optional[bytes] = None):
        if data is not None:
            self._data = bytearray(data)
            self._length = len(data)
        else:
            self._data = bytearray(capacity)
            self._length = capacity
        self._pos = 0

    @property
    def capacity(self) -> int:
        return self._length

    @property
    def data(self) -> bytes:
        return bytes(self._data[: self._pos])

    def data_slice(self, start: int, end: int) -> bytes:
        return bytes(self._data[start:end])

    def eof(self) -> bool:
        return self._pos == self._length

    def seek(self, pos: int) -> None:
        assert pos <= self._length
        self._pos = pos

    def tell(self) -> int:
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
    return bytes(v)


def push_bytes(buf: Buffer, v: bytes) -> None:
    """
    Push bytes.
    """
    length = len(v)
    if buf._pos + length > buf._length:
        raise BufferWriteError
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


def push_uint8(buf: Buffer, v: int) -> None:
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


def push_uint16(buf: Buffer, v: int) -> None:
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


def push_uint32(buf: Buffer, v: int) -> None:
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


def push_uint64(buf: Buffer, v: int) -> None:
    """
    Push a 64-bit unsigned integer.
    """
    pack_into("!Q", buf._data, buf._pos, v)
    buf._pos += 8


def pull_uint_var(buf: Buffer) -> int:
    """
    Pull a QUIC variable-length unsigned integer.
    """
    try:
        kind = buf._data[buf._pos] // 64
        if kind == 0:
            value = buf._data[buf._pos]
            buf._pos += 1
            return value
        elif kind == 1:
            value, = unpack_from("!H", buf._data, buf._pos)
            buf._pos += 2
            return value & 0x3FFF
        elif kind == 2:
            value, = unpack_from("!L", buf._data, buf._pos)
            buf._pos += 4
            return value & 0x3FFFFFFF
        else:
            value, = unpack_from("!Q", buf._data, buf._pos)
            buf._pos += 8
            return value & 0x3FFFFFFFFFFFFFFF
    except (IndexError, struct.error):
        raise BufferReadError


def push_uint_var(buf: Buffer, value: int) -> None:
    """
    Push a QUIC variable-length unsigned integer.
    """
    if value <= 0x3F:
        buf._data[buf._pos] = value
        buf._pos += 1
    elif value <= 0x3FFF:
        pack_into("!H", buf._data, buf._pos, value | 0x4000)
        buf._pos += 2
    elif value <= 0x3FFFFFFF:
        pack_into("!L", buf._data, buf._pos, value | 0x80000000)
        buf._pos += 4
    elif value <= 0x3FFFFFFFFFFFFFFF:
        pack_into("!Q", buf._data, buf._pos, value | 0xC000000000000000)
        buf._pos += 8
    else:
        raise ValueError("Integer is too big for a variable-length integer")


def size_uint_var(value: int) -> int:
    """
    Returns the number of bytes required to encode the given value
    as a QUIC variable-length unsigned integer.
    """
    if value <= 0x3F:
        return 1
    elif value <= 0x3FFF:
        return 2
    elif value <= 0x3FFFFFFF:
        return 4
    elif value <= 0x3FFFFFFFFFFFFFFF:
        return 8
    else:
        raise ValueError("Integer is too big for a variable-length integer")
