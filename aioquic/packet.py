from dataclasses import dataclass

from .tls import pull_bytes, pull_uint8, pull_uint32, pull_uint_var

PACKET_LONG_HEADER = 0x80
PACKET_FIXED_BIT = 0x40

PACKET_TYPE_INITIAL = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x00
PACKET_TYPE_0RTT = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x10
PACKET_TYPE_HANDSHAKE = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x20
PACKET_TYPE_RETRY = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x30
PACKET_TYPE_MASK = 0xf0

PROTOCOL_VERSION = 0xFF000011  # draft 17


def decode_cid_length(length):
    return length + 3 if length else 0


def is_long_header(first_byte):
    return bool(first_byte & PACKET_LONG_HEADER)


@dataclass
class QuicHeader:
    version: int
    destination_cid: bytes
    source_cid: bytes
    encrypted_offset: int
    token: bytes = b''


def pull_quic_header(buf):
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
            pull_uint_var(buf)

        return QuicHeader(
            version=version,
            destination_cid=destination_cid,
            source_cid=source_cid,
            encrypted_offset=buf._pos,
            token=token)
    else:
        # short header packet
        raise ValueError('Short header is not supported yet')
