import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple

from .buffer import (
    Buffer,
    pull_bytes,
    pull_uint8,
    pull_uint16,
    pull_uint32,
    pull_uint_var,
    push_bytes,
    push_uint8,
    push_uint16,
    push_uint32,
    push_uint_var,
)
from .rangeset import RangeSet
from .tls import pull_block, push_block

PACKET_LONG_HEADER = 0x80
PACKET_FIXED_BIT = 0x40
PACKET_SPIN_BIT = 0x20

PACKET_TYPE_INITIAL = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x00
PACKET_TYPE_ZERO_RTT = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x10
PACKET_TYPE_HANDSHAKE = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x20
PACKET_TYPE_RETRY = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x30
PACKET_TYPE_ONE_RTT = PACKET_FIXED_BIT
PACKET_TYPE_MASK = 0xF0

PACKET_NUMBER_MAX_SIZE = 4


class QuicErrorCode(IntEnum):
    NO_ERROR = 0x0
    INTERNAL_ERROR = 0x1
    SERVER_BUSY = 0x2
    FLOW_CONTROL_ERROR = 0x3
    STREAM_LIMIT_ERROR = 0x4
    STREAM_STATE_ERROR = 0x5
    FINAL_SIZE_ERROR = 0x6
    FRAME_ENCODING_ERROR = 0x7
    TRANSPORT_PARAMETER_ERROR = 0x8
    PROTOCOL_VIOLATION = 0xA
    INVALID_MIGRATION = 0xC
    CRYPTO_BUFFER_EXCEEDED = 0xD
    CRYPTO_ERROR = 0x100


class QuicProtocolVersion(IntEnum):
    NEGOTIATION = 0
    DRAFT_17 = 0xFF000011
    DRAFT_18 = 0xFF000012
    DRAFT_19 = 0xFF000013
    DRAFT_20 = 0xFF000014


@dataclass
class QuicHeader:
    is_long_header: bool
    version: Optional[int]
    packet_type: int
    destination_cid: bytes
    source_cid: bytes
    original_destination_cid: bytes = b""
    token: bytes = b""
    rest_length: int = 0


def decode_cid_length(length: int) -> int:
    return length + 3 if length else 0


def decode_packet_number(truncated: int, num_bits: int, expected: int) -> int:
    """
    Recover a packet number from a truncated packet number.

    See: Appendix A - Sample Packet Number Decoding Algorithm
    """
    window = 1 << num_bits
    half_window = window // 2
    candidate = (expected & ~(window - 1)) | truncated
    if candidate <= expected - half_window:
        return candidate + window
    elif candidate > expected + half_window and candidate > window:
        return candidate - window
    else:
        return candidate


def encode_cid_length(length: int) -> int:
    return length - 3 if length else 0


def get_spin_bit(first_byte: int) -> bool:
    return bool(first_byte & PACKET_SPIN_BIT)


def is_long_header(first_byte: int) -> bool:
    return bool(first_byte & PACKET_LONG_HEADER)


def pull_quic_header(buf: Buffer, host_cid_length: Optional[int] = None) -> QuicHeader:
    first_byte = pull_uint8(buf)

    original_destination_cid = b""
    token = b""
    if is_long_header(first_byte):
        # long header packet
        version = pull_uint32(buf)
        cid_lengths = pull_uint8(buf)

        destination_cid_length = decode_cid_length(cid_lengths // 16)
        destination_cid = pull_bytes(buf, destination_cid_length)

        source_cid_length = decode_cid_length(cid_lengths % 16)
        source_cid = pull_bytes(buf, source_cid_length)

        if version == QuicProtocolVersion.NEGOTIATION:
            # version negotiation
            packet_type = None
            rest_length = buf.capacity - buf.tell()
        else:
            if not (first_byte & PACKET_FIXED_BIT):
                raise ValueError("Packet fixed bit is zero")

            packet_type = first_byte & PACKET_TYPE_MASK
            if packet_type == PACKET_TYPE_INITIAL:
                token_length = pull_uint_var(buf)
                token = pull_bytes(buf, token_length)
                rest_length = pull_uint_var(buf)
            elif packet_type == PACKET_TYPE_RETRY:
                original_destination_cid_length = decode_cid_length(first_byte & 0xF)
                original_destination_cid = pull_bytes(
                    buf, original_destination_cid_length
                )
                token = pull_bytes(buf, buf.capacity - buf.tell())
                rest_length = 0
            else:
                rest_length = pull_uint_var(buf)

        return QuicHeader(
            is_long_header=True,
            version=version,
            packet_type=packet_type,
            destination_cid=destination_cid,
            source_cid=source_cid,
            original_destination_cid=original_destination_cid,
            token=token,
            rest_length=rest_length,
        )
    else:
        # short header packet
        if not (first_byte & PACKET_FIXED_BIT):
            raise ValueError("Packet fixed bit is zero")

        packet_type = first_byte & PACKET_TYPE_MASK
        destination_cid = pull_bytes(buf, host_cid_length)
        return QuicHeader(
            is_long_header=False,
            version=None,
            packet_type=packet_type,
            destination_cid=destination_cid,
            source_cid=b"",
            token=b"",
            rest_length=buf.capacity - buf.tell(),
        )


def encode_quic_retry(
    version: int,
    source_cid: bytes,
    destination_cid: bytes,
    original_destination_cid: bytes,
    retry_token: bytes,
) -> bytes:
    buf = Buffer(
        capacity=6
        + len(destination_cid)
        + len(source_cid)
        + len(original_destination_cid)
        + len(retry_token)
    )
    push_uint8(
        buf, PACKET_TYPE_RETRY | encode_cid_length(len(original_destination_cid))
    )
    push_uint32(buf, version)
    push_uint8(
        buf,
        (encode_cid_length(len(destination_cid)) << 4)
        | encode_cid_length(len(source_cid)),
    )
    push_bytes(buf, destination_cid)
    push_bytes(buf, source_cid)
    push_bytes(buf, original_destination_cid)
    push_bytes(buf, retry_token)
    return buf.data


def encode_quic_version_negotiation(
    source_cid: bytes,
    destination_cid: bytes,
    supported_versions: List[QuicProtocolVersion],
) -> bytes:
    buf = Buffer(
        capacity=6
        + len(destination_cid)
        + len(source_cid)
        + 4 * len(supported_versions)
    )
    push_uint8(buf, os.urandom(1)[0] | PACKET_LONG_HEADER)
    push_uint32(buf, QuicProtocolVersion.NEGOTIATION)
    push_uint8(
        buf,
        (encode_cid_length(len(destination_cid)) << 4)
        | encode_cid_length(len(source_cid)),
    )
    push_bytes(buf, destination_cid)
    push_bytes(buf, source_cid)
    for version in supported_versions:
        push_uint32(buf, version)
    return buf.data


# TLS EXTENSION


@dataclass
class QuicTransportParameters:
    initial_version: Optional[QuicProtocolVersion] = None
    negotiated_version: Optional[QuicProtocolVersion] = None
    supported_versions: List[QuicProtocolVersion] = field(default_factory=list)

    original_connection_id: Optional[bytes] = None
    idle_timeout: Optional[int] = None
    stateless_reset_token: Optional[bytes] = None
    max_packet_size: Optional[int] = None
    initial_max_data: Optional[int] = None
    initial_max_stream_data_bidi_local: Optional[int] = None
    initial_max_stream_data_bidi_remote: Optional[int] = None
    initial_max_stream_data_uni: Optional[int] = None
    initial_max_streams_bidi: Optional[int] = None
    initial_max_streams_uni: Optional[int] = None
    ack_delay_exponent: Optional[int] = None
    max_ack_delay: Optional[int] = None
    disable_migration: Optional[bool] = False
    preferred_address: Optional[bytes] = None


PARAMS = [
    ("original_connection_id", bytes),
    ("idle_timeout", int),
    ("stateless_reset_token", bytes),
    ("max_packet_size", int),
    ("initial_max_data", int),
    ("initial_max_stream_data_bidi_local", int),
    ("initial_max_stream_data_bidi_remote", int),
    ("initial_max_stream_data_uni", int),
    ("initial_max_streams_bidi", int),
    ("initial_max_streams_uni", int),
    ("ack_delay_exponent", int),
    ("max_ack_delay", int),
    ("disable_migration", bool),
    ("preferred_address", bytes),
]


def pull_quic_transport_parameters(buf: Buffer) -> QuicTransportParameters:
    params = QuicTransportParameters()

    with pull_block(buf, 2) as length:
        end = buf.tell() + length
        while buf.tell() < end:
            param_id = pull_uint16(buf)
            param_len = pull_uint16(buf)
            param_start = buf.tell()
            if param_id < len(PARAMS):
                # parse known parameter
                param_name, param_type = PARAMS[param_id]
                if param_type == int:
                    setattr(params, param_name, pull_uint_var(buf))
                elif param_type == bytes:
                    setattr(params, param_name, pull_bytes(buf, param_len))
                else:
                    setattr(params, param_name, True)
            else:
                # skip unknown parameter
                pull_bytes(buf, param_len)
            assert buf.tell() == param_start + param_len

    return params


def push_quic_transport_parameters(
    buf: Buffer, params: QuicTransportParameters
) -> None:
    with push_block(buf, 2):
        for param_id, (param_name, param_type) in enumerate(PARAMS):
            param_value = getattr(params, param_name)
            if param_value is not None and param_value is not False:
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
    PATH_CHALLENGE = 0x1A
    PATH_RESPONSE = 0x1B
    TRANSPORT_CLOSE = 0x1C
    APPLICATION_CLOSE = 0x1D


NON_ACK_ELICITING_FRAME_TYPES = frozenset(
    [QuicFrameType.ACK, QuicFrameType.ACK_ECN, QuicFrameType.PADDING]
)
PROBING_FRAME_TYPES = frozenset(
    [
        QuicFrameType.PATH_CHALLENGE,
        QuicFrameType.PATH_RESPONSE,
        QuicFrameType.PADDING,
        QuicFrameType.NEW_CONNECTION_ID,
    ]
)


def pull_ack_frame(buf: Buffer) -> Tuple[RangeSet, int]:
    rangeset = RangeSet()
    end = pull_uint_var(buf)  # largest acknowledged
    delay = pull_uint_var(buf)
    ack_range_count = pull_uint_var(buf)
    ack_count = pull_uint_var(buf)  # first ack range
    rangeset.add(end - ack_count, end + 1)
    end -= ack_count
    for _ in range(ack_range_count):
        end -= pull_uint_var(buf) + 2
        ack_count = pull_uint_var(buf)
        rangeset.add(end - ack_count, end + 1)
        end -= ack_count
    return rangeset, delay


def push_ack_frame(buf: Buffer, rangeset: RangeSet, delay: int) -> None:
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
        push_uint_var(buf, start - r.stop - 1)
        push_uint_var(buf, r.stop - r.start - 1)
        start = r.start


@dataclass
class QuicStreamFrame:
    data: bytes = b""
    fin: bool = False
    offset: int = 0


def pull_crypto_frame(buf: Buffer) -> QuicStreamFrame:
    offset = pull_uint_var(buf)
    length = pull_uint_var(buf)
    return QuicStreamFrame(offset=offset, data=pull_bytes(buf, length))


def pull_new_token_frame(buf: Buffer) -> bytes:
    length = pull_uint_var(buf)
    return pull_bytes(buf, length)


def push_new_token_frame(buf: Buffer, token: bytes) -> None:
    push_uint_var(buf, len(token))
    push_bytes(buf, token)


def pull_new_connection_id_frame(buf: Buffer) -> Tuple[int, bytes, bytes]:
    sequence_number = pull_uint_var(buf)
    length = pull_uint8(buf)
    connection_id = pull_bytes(buf, length)
    stateless_reset_token = pull_bytes(buf, 16)
    return (sequence_number, connection_id, stateless_reset_token)


def push_new_connection_id_frame(
    buf: Buffer,
    sequence_number: int,
    connection_id: bytes,
    stateless_reset_token: bytes,
) -> None:
    assert len(stateless_reset_token) == 16
    push_uint_var(buf, sequence_number)
    push_uint8(buf, len(connection_id))
    push_bytes(buf, connection_id)
    push_bytes(buf, stateless_reset_token)


def decode_reason_phrase(reason_bytes: bytes) -> str:
    try:
        return reason_bytes.decode("utf8")
    except UnicodeDecodeError:
        return ""


def pull_transport_close_frame(buf: Buffer) -> Tuple[int, int, str]:
    error_code = pull_uint16(buf)
    frame_type = pull_uint_var(buf)
    reason_length = pull_uint_var(buf)
    reason_phrase = decode_reason_phrase(pull_bytes(buf, reason_length))
    return (error_code, frame_type, reason_phrase)


def pull_application_close_frame(buf: Buffer) -> Tuple[int, str]:
    error_code = pull_uint16(buf)
    reason_length = pull_uint_var(buf)
    reason_phrase = decode_reason_phrase(pull_bytes(buf, reason_length))
    return (error_code, reason_phrase)
