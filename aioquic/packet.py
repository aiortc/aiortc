from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Tuple

from .tls import (BufferReadError, pull_bytes, pull_uint8, pull_uint16,
                  pull_uint32, pull_uint64, push_bytes, push_uint8,
                  push_uint16, push_uint32, push_uint64)

PACKET_LONG_HEADER = 0x80
PACKET_FIXED_BIT = 0x40

PACKET_TYPE_INITIAL = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x00
PACKET_TYPE_0RTT = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x10
PACKET_TYPE_HANDSHAKE = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x20
PACKET_TYPE_RETRY = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x30
PACKET_TYPE_MASK = 0xf0

PROTOCOL_VERSION_DRAFT_17 = 0xff000011  # draft 17

UINT_VAR_FORMATS = [
    (pull_uint8, push_uint8, 0x3f),
    (pull_uint16, push_uint16, 0x3fff),
    (pull_uint32, push_uint32, 0x3fffffff),
    (pull_uint64, push_uint64, 0x3fffffffffffffff),
]


@dataclass
class QuicHeader:
    version: int
    packet_type: int
    destination_cid: bytes
    source_cid: bytes
    token: bytes = b''
    rest_length: int = 0


@dataclass
class QuicShortHeader:
    packet_type: int
    destination_cid: bytes


@dataclass
class QuickAckFrame:
    largest_acknowledged: int
    ack_delay: int
    first_ack_range: int
    ack_ranges: List[Tuple[int, int]]


def decode_cid_length(length):
    return length + 3 if length else 0


def encode_cid_length(length):
    return length - 3 if length else 0


def is_long_header(first_byte):
    return bool(first_byte & PACKET_LONG_HEADER)


def pull_uint_var(buf):
    """
    Pull a QUIC variable-length unsigned integer.
    """
    try:
        kind = buf._data[buf._pos] // 64
    except IndexError:
        raise BufferReadError
    pull, push, mask = UINT_VAR_FORMATS[kind]
    return pull(buf) & mask


def push_uint_var(buf, value):
    """
    Push a QUIC variable-length unsigned integer.
    """
    for i, (pull, push, mask) in enumerate(UINT_VAR_FORMATS):
        if value <= mask:
            start = buf._pos
            push(buf, value)
            buf._data[start] |= i * 64
            return
    raise ValueError('Integer is too big for a variable-length integer')


def pull_quic_header(buf, host_cid_length=None):
    first_byte = pull_uint8(buf)
    if not (first_byte & PACKET_FIXED_BIT):
        raise ValueError('Packet fixed bit is zero')

    token = b''
    if is_long_header(first_byte):
        version = pull_uint32(buf)
        cid_lengths = pull_uint8(buf)

        destination_cid_length = decode_cid_length(cid_lengths // 16)
        destination_cid = pull_bytes(buf, destination_cid_length)

        source_cid_length = decode_cid_length(cid_lengths % 16)
        source_cid = pull_bytes(buf, source_cid_length)

        packet_type = first_byte & PACKET_TYPE_MASK
        if packet_type == PACKET_TYPE_INITIAL:
            token_length = pull_uint_var(buf)
            token = pull_bytes(buf, token_length)
        rest_length = pull_uint_var(buf)

        return QuicHeader(
            version=version,
            packet_type=packet_type,
            destination_cid=destination_cid,
            source_cid=source_cid,
            token=token,
            rest_length=rest_length)
    else:
        # short header packet
        packet_type = first_byte & PACKET_TYPE_MASK
        destination_cid = pull_bytes(buf, host_cid_length)
        return QuicHeader(
            version=0,
            packet_type=packet_type,
            destination_cid=destination_cid,
            source_cid=b'',
            token=b'',
            rest_length=buf.capacity - buf.tell())


def push_quic_header(buf, header):
    push_uint8(buf, header.packet_type)
    push_uint32(buf, header.version)
    push_uint8(buf,
               (encode_cid_length(len(header.destination_cid)) << 4) |
               encode_cid_length(len(header.source_cid)))
    push_bytes(buf, header.destination_cid)
    push_bytes(buf, header.source_cid)
    if (header.packet_type & PACKET_TYPE_MASK) == PACKET_TYPE_INITIAL:
        push_uint_var(buf, len(header.token))
        push_bytes(buf, header.token)
    push_uint16(buf, 0)  # length
    push_uint16(buf, 0)  # pn


def pull_ack_frame(buf):
    largest_acknowledged = pull_uint_var(buf)
    ack_delay = pull_uint_var(buf)
    ack_range_count = pull_uint_var(buf)
    first_ack_range = pull_uint_var(buf)
    ack = QuickAckFrame(
        largest_acknowledged=largest_acknowledged,
        ack_delay=ack_delay,
        first_ack_range=first_ack_range,
        ack_ranges=[])
    for _ in range(ack_range_count):
        ack.ack_ranges.append((
            pull_uint_var(buf),
            pull_uint_var(buf)))
    return ack


def push_ack_frame(buf, ack):
    push_uint_var(buf, ack.largest_acknowledged)
    push_uint_var(buf, ack.ack_delay)
    push_uint_var(buf, len(ack.ack_ranges))
    push_uint_var(buf, ack.first_ack_range)
    for r in ack.ack_ranges:
        push_uint_var(buf, r[0])
        push_uint_var(buf, r[1])


def pull_crypto_frame(buf):
    pull_uint_var(buf)
    length = pull_uint_var(buf)
    return pull_bytes(buf, length)


@contextmanager
def push_crypto_frame(buf, offset=0):
    push_uint_var(buf, offset)
    push_uint16(buf, 0)
    start = buf.tell()
    yield
    end = buf.tell()
    buf.seek(start - 2)
    push_uint16(buf, (end - start) | 0x4000)
    buf.seek(end)
