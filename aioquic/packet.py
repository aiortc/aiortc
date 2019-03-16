from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List

from .rangeset import RangeSet
from .tls import (BufferReadError, pull_block, pull_bytes, pull_list,
                  pull_uint8, pull_uint16, pull_uint32, pull_uint64,
                  push_block, push_bytes, push_list, push_uint8, push_uint16,
                  push_uint32, push_uint64)

PACKET_LONG_HEADER = 0x80
PACKET_FIXED_BIT = 0x40

PACKET_TYPE_INITIAL = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x00
PACKET_TYPE_0RTT = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x10
PACKET_TYPE_HANDSHAKE = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x20
PACKET_TYPE_RETRY = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x30
PACKET_TYPE_MASK = 0xf0

PROTOCOL_VERSION_NEGOTIATION = 0
PROTOCOL_VERSION_DRAFT_17 = 0xff000011
PROTOCOL_VERSION_DRAFT_18 = 0xff000012

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

    token = b''
    if is_long_header(first_byte):
        # long header packet
        version = pull_uint32(buf)
        cid_lengths = pull_uint8(buf)

        destination_cid_length = decode_cid_length(cid_lengths // 16)
        destination_cid = pull_bytes(buf, destination_cid_length)

        source_cid_length = decode_cid_length(cid_lengths % 16)
        source_cid = pull_bytes(buf, source_cid_length)

        if version == PROTOCOL_VERSION_NEGOTIATION:
            # version negotiation
            packet_type = None
            rest_length = buf.capacity - buf.tell()
        else:
            if version and not (first_byte & PACKET_FIXED_BIT):
                raise ValueError('Packet fixed bit is zero')

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
        if not (first_byte & PACKET_FIXED_BIT):
            raise ValueError('Packet fixed bit is zero')

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


# TLS EXTENSION


@dataclass
class QuicTransportParameters:
    initial_version: int = None
    negotiated_version: int = None
    supported_versions: List[int] = field(default_factory=list)

    original_connection_id: bytes = None
    idle_timeout: int = None
    stateless_reset_token: bytes = None
    max_packet_size: int = None
    initial_max_data: int = None
    initial_max_stream_data_bidi_local: int = None
    initial_max_stream_data_bidi_remote: int = None
    initial_max_stream_data_uni: int = None
    initial_max_streams_bidi: int = None
    initial_max_streams_uni: int = None
    ack_delay_exponent: int = None
    max_ack_delay: int = None
    disable_migration: bool = False
    preferred_address: bytes = None


PARAMS = [
    ('original_connection_id', bytes),
    ('idle_timeout', int),
    ('stateless_reset_token', bytes),
    ('max_packet_size', int),
    ('initial_max_data', int),
    ('initial_max_stream_data_bidi_local', int),
    ('initial_max_stream_data_bidi_remote', int),
    ('initial_max_stream_data_uni', int),
    ('initial_max_streams_bidi', int),
    ('initial_max_streams_uni', int),
    ('ack_delay_exponent', int),
    ('max_ack_delay', int),
    ('disable_migration', bool),
    ('preferred_address', bytes),
]


def pull_quic_transport_parameters(buf, is_client):
    params = QuicTransportParameters()
    if is_client:
        params.initial_version = pull_uint32(buf)
    else:
        params.negotiated_version = pull_uint32(buf)
        params.supported_versions = pull_list(buf, 1, pull_uint32)
    with pull_block(buf, 2) as length:
        end = buf.tell() + length
        while buf.tell() < end:
            param_id = pull_uint16(buf)
            param_len = pull_uint16(buf)
            param_start = buf.tell()
            param_name, param_type = PARAMS[param_id]
            if param_type == int:
                setattr(params, param_name, pull_uint_var(buf))
            elif param_type == bytes:
                setattr(params, param_name, pull_bytes(buf, param_len))
            else:
                setattr(params, param_name, True)
            assert buf.tell() == param_start + param_len

    return params


def push_quic_transport_parameters(buf, params, is_client):
    if is_client:
        push_uint32(buf, params.initial_version)
    else:
        push_uint32(buf, params.negotiated_version)
        push_list(buf, 1, push_uint32, params.supported_versions)
    with push_block(buf, 2):
        for param_id, (param_name, param_type) in enumerate(PARAMS):
            param_value = getattr(params, param_name)
            if param_value not in [None, False]:
                push_uint16(buf, param_id)
                with push_block(buf, 2):
                    if param_type == int:
                        push_uint_var(buf, param_value)
                    elif param_type == bytes:
                        push_bytes(buf, param_value)


# FRAMES


class QuicFrameType(IntEnum):
    PADDING = 0x00
    PING = 0x01
    ACK = 0x02
    ACK_ECN = 0x03
    RESET_STREAM = 0x04
    STOP_SENDING = 0x05
    CRYPTO = 0x06
    NEW_TOKEN = 0x07
    STREAM_BASE = 0x08
    MAX_DATA = 0x10
    MAX_STREAM_DATA = 0x11
    MAX_STREAMS_BIDI = 0x12
    MAX_STREAMS_UNI = 0x13
    DATA_BLOCKED = 0x14
    STREAM_DATA_BLOCKED = 0x15
    STREAMS_BLOCKED_BIDI = 0x16
    STREAMS_BLOCKED_UNI = 0x17
    NEW_CONNECTION_ID = 0x18
    RETIRE_CONNECTION_ID = 0x19
    PATH_CHALLENGE = 0x1a
    PATH_RESPONSE = 0x1b
    TRANSPORT_CLOSE = 0x1c
    APPLICATION_CLOSE = 0x1d


def pull_ack_frame(buf):
    rangeset = RangeSet()
    end = pull_uint_var(buf)  # largest acknowledged
    delay = pull_uint_var(buf)
    ack_range_count = pull_uint_var(buf)
    ack_count = pull_uint_var(buf)  # first ack range
    rangeset.add(end - ack_count, end + 1)
    end -= ack_count
    for _ in range(ack_range_count):
        end -= pull_uint_var(buf)
        ack_count = pull_uint_var(buf)
        rangeset.add(end - ack_count, end + 1)
        end -= ack_count
    return rangeset, delay


def push_ack_frame(buf, rangeset: RangeSet, delay: int):
    index = len(rangeset) - 1
    r = rangeset[index]
    push_uint_var(buf, r.stop - 1)
    push_uint_var(buf, delay)
    push_uint_var(buf, index)
    push_uint_var(buf, r.stop - 1 - r.start)
    start = r.start
    while index > 0:
        index -= 1
        r = rangeset[index]
        push_uint_var(buf, start - r.stop + 1)
        push_uint_var(buf, r.stop - r.start - 1)
        start = r.start


@dataclass
class QuicStreamFrame:
    data: bytes = b''
    offset: int = 0


def pull_crypto_frame(buf):
    offset = pull_uint_var(buf)
    length = pull_uint_var(buf)
    return QuicStreamFrame(offset=offset, data=pull_bytes(buf, length))


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


@contextmanager
def push_stream_frame(buf, stream_id, offset):
    push_uint_var(buf, stream_id)
    push_uint_var(buf, offset)
    push_uint16(buf, 0)
    start = buf.tell()
    yield
    end = buf.tell()
    buf.seek(start - 2)
    push_uint16(buf, (end - start) | 0x4000)
    buf.seek(end)


def pull_new_connection_id_frame(buf):
    sequence_number = pull_uint_var(buf)
    length = pull_uint8(buf)
    connection_id = pull_bytes(buf, length)
    stateless_reset_token = pull_bytes(buf, 16)
    return (sequence_number, connection_id, stateless_reset_token)


def push_new_connection_id_frame(buf, sequence_number, connection_id, stateless_reset_token):
    assert len(stateless_reset_token) == 16
    push_uint_var(buf, sequence_number)
    push_uint8(buf, len(connection_id))
    push_bytes(buf, connection_id)
    push_bytes(buf, stateless_reset_token)
