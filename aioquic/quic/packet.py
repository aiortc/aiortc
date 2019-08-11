import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple

from ..buffer import Buffer
from ..tls import pull_block, push_block
from .rangeset import RangeSet

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
    DRAFT_21 = 0xFF000015
    DRAFT_22 = 0xFF000016


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


def get_spin_bit(first_byte: int) -> bool:
    return bool(first_byte & PACKET_SPIN_BIT)


def is_long_header(first_byte: int) -> bool:
    return bool(first_byte & PACKET_LONG_HEADER)


def pull_quic_header(buf: Buffer, host_cid_length: Optional[int] = None) -> QuicHeader:
    first_byte = buf.pull_uint8()

    original_destination_cid = b""
    token = b""
    if is_long_header(first_byte):
        # long header packet
        version = buf.pull_uint32()

        destination_cid_length = buf.pull_uint8()
        destination_cid = buf.pull_bytes(destination_cid_length)

        source_cid_length = buf.pull_uint8()
        source_cid = buf.pull_bytes(source_cid_length)

        if version == QuicProtocolVersion.NEGOTIATION:
            # version negotiation
            packet_type = None
            rest_length = buf.capacity - buf.tell()
        else:
            if not (first_byte & PACKET_FIXED_BIT):
                raise ValueError("Packet fixed bit is zero")

            packet_type = first_byte & PACKET_TYPE_MASK
            if packet_type == PACKET_TYPE_INITIAL:
                token_length = buf.pull_uint_var()
                token = buf.pull_bytes(token_length)
                rest_length = buf.pull_uint_var()
            elif packet_type == PACKET_TYPE_RETRY:
                original_destination_cid_length = buf.pull_uint8()
                original_destination_cid = buf.pull_bytes(
                    original_destination_cid_length
                )
                token = buf.pull_bytes(buf.capacity - buf.tell())
                rest_length = 0
            else:
                rest_length = buf.pull_uint_var()

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
        destination_cid = buf.pull_bytes(host_cid_length)
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
        capacity=8
        + len(destination_cid)
        + len(source_cid)
        + len(original_destination_cid)
        + len(retry_token)
    )
    buf.push_uint8(PACKET_TYPE_RETRY)
    buf.push_uint32(version)
    buf.push_uint8(len(destination_cid))
    buf.push_bytes(destination_cid)
    buf.push_uint8(len(source_cid))
    buf.push_bytes(source_cid)
    buf.push_uint8(len(original_destination_cid))
    buf.push_bytes(original_destination_cid)
    buf.push_bytes(retry_token)
    return buf.data


def encode_quic_version_negotiation(
    source_cid: bytes, destination_cid: bytes, supported_versions: List[int]
) -> bytes:
    buf = Buffer(
        capacity=7
        + len(destination_cid)
        + len(source_cid)
        + 4 * len(supported_versions)
    )
    buf.push_uint8(os.urandom(1)[0] | PACKET_LONG_HEADER)
    buf.push_uint32(QuicProtocolVersion.NEGOTIATION)
    buf.push_uint8(len(destination_cid))
    buf.push_bytes(destination_cid)
    buf.push_uint8(len(source_cid))
    buf.push_bytes(source_cid)
    for version in supported_versions:
        buf.push_uint32(version)
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
    active_connection_id_limit: Optional[int] = None


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
    ("active_connection_id_limit", int),
]


def pull_quic_transport_parameters(buf: Buffer) -> QuicTransportParameters:
    params = QuicTransportParameters()

    with pull_block(buf, 2) as length:
        end = buf.tell() + length
        while buf.tell() < end:
            param_id = buf.pull_uint16()
            param_len = buf.pull_uint16()
            param_start = buf.tell()
            if param_id < len(PARAMS):
                # parse known parameter
                param_name, param_type = PARAMS[param_id]
                if param_type == int:
                    setattr(params, param_name, buf.pull_uint_var())
                elif param_type == bytes:
                    setattr(params, param_name, buf.pull_bytes(param_len))
                else:
                    setattr(params, param_name, True)
            else:
                # skip unknown parameter
                buf.pull_bytes(param_len)
            assert buf.tell() == param_start + param_len

    return params


def push_quic_transport_parameters(
    buf: Buffer, params: QuicTransportParameters
) -> None:
    with push_block(buf, 2):
        for param_id, (param_name, param_type) in enumerate(PARAMS):
            param_value = getattr(params, param_name)
            if param_value is not None and param_value is not False:
                buf.push_uint16(param_id)
                with push_block(buf, 2):
                    if param_type == int:
                        buf.push_uint_var(param_value)
                    elif param_type == bytes:
                        buf.push_bytes(param_value)


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
    end = buf.pull_uint_var()  # largest acknowledged
    delay = buf.pull_uint_var()
    ack_range_count = buf.pull_uint_var()
    ack_count = buf.pull_uint_var()  # first ack range
    rangeset.add(end - ack_count, end + 1)
    end -= ack_count
    for _ in range(ack_range_count):
        end -= buf.pull_uint_var() + 2
        ack_count = buf.pull_uint_var()
        rangeset.add(end - ack_count, end + 1)
        end -= ack_count
    return rangeset, delay


def push_ack_frame(buf: Buffer, rangeset: RangeSet, delay: int) -> None:
    index = len(rangeset) - 1
    r = rangeset[index]
    buf.push_uint_var(r.stop - 1)
    buf.push_uint_var(delay)
    buf.push_uint_var(index)
    buf.push_uint_var(r.stop - 1 - r.start)
    start = r.start
    while index > 0:
        index -= 1
        r = rangeset[index]
        buf.push_uint_var(start - r.stop - 1)
        buf.push_uint_var(r.stop - r.start - 1)
        start = r.start


@dataclass
class QuicStreamFrame:
    data: bytes = b""
    fin: bool = False
    offset: int = 0


def pull_crypto_frame(buf: Buffer) -> QuicStreamFrame:
    offset = buf.pull_uint_var()
    length = buf.pull_uint_var()
    return QuicStreamFrame(offset=offset, data=buf.pull_bytes(length))


def pull_new_token_frame(buf: Buffer) -> bytes:
    length = buf.pull_uint_var()
    return buf.pull_bytes(length)


def push_new_token_frame(buf: Buffer, token: bytes) -> None:
    buf.push_uint_var(len(token))
    buf.push_bytes(token)


def pull_new_connection_id_frame(buf: Buffer) -> Tuple[int, int, bytes, bytes]:
    sequence_number = buf.pull_uint_var()
    retire_prior_to = buf.pull_uint_var()
    length = buf.pull_uint8()
    connection_id = buf.pull_bytes(length)
    stateless_reset_token = buf.pull_bytes(16)
    return (sequence_number, retire_prior_to, connection_id, stateless_reset_token)


def push_new_connection_id_frame(
    buf: Buffer,
    sequence_number: int,
    retire_prior_to: int,
    connection_id: bytes,
    stateless_reset_token: bytes,
) -> None:
    assert len(stateless_reset_token) == 16
    buf.push_uint_var(sequence_number)
    buf.push_uint_var(retire_prior_to)
    buf.push_uint8(len(connection_id))
    buf.push_bytes(connection_id)
    buf.push_bytes(stateless_reset_token)


def decode_reason_phrase(reason_bytes: bytes) -> str:
    try:
        return reason_bytes.decode("utf8")
    except UnicodeDecodeError:
        return ""


def pull_transport_close_frame(buf: Buffer) -> Tuple[int, int, str]:
    error_code = buf.pull_uint_var()
    frame_type = buf.pull_uint_var()
    reason_length = buf.pull_uint_var()
    reason_phrase = decode_reason_phrase(buf.pull_bytes(reason_length))
    return (error_code, frame_type, reason_phrase)


def pull_application_close_frame(buf: Buffer) -> Tuple[int, str]:
    error_code = buf.pull_uint_var()
    reason_length = buf.pull_uint_var()
    reason_phrase = decode_reason_phrase(buf.pull_bytes(reason_length))
    return (error_code, reason_phrase)
