from dataclasses import dataclass
from struct import unpack_from

PACKET_LONG_HEADER = 0x80
PACKET_FIXED_BIT = 0x40

PACKET_TYPE_INITIAL = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x00
PACKET_TYPE_0RTT = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x10
PACKET_TYPE_HANDSHAKE = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x20
PACKET_TYPE_RETRY = PACKET_LONG_HEADER | PACKET_FIXED_BIT | 0x30
PACKET_TYPE_MASK = 0xf0

PROTOCOL_VERSION = 0xFF000011  # draft 17

VARIABLE_LENGTH_FORMATS = [
    (1, '!B', 0x3f),
    (2, '!H', 0x3fff),
    (4, '!L', 0x3fffffff),
    (8, '!Q', 0x3fffffffffffffff),
]


def decode_cid_length(length):
    return length + 3 if length else 0


def is_long_header(first_byte):
    return bool(first_byte & PACKET_LONG_HEADER)


def unpack_variable_length(data, pos=0):
    kind = data[pos] // 64
    length, fmt, mask = VARIABLE_LENGTH_FORMATS[kind]
    return unpack_from(fmt, data, pos)[0] & mask, pos + length


@dataclass
class QuicHeader:
    version: int
    destination_cid: bytes
    source_cid: bytes
    encrypted_offset: int
    token: bytes = b''

    @classmethod
    def parse(cls, data):
        datagram_length = len(data)
        if datagram_length < 2:
            raise ValueError('Packet is too short (%d bytes)' % datagram_length)

        first_byte = data[0]
        if not (first_byte & PACKET_FIXED_BIT):
            raise ValueError('Packet fixed bit is zero')

        token = b''
        if is_long_header(first_byte):
            if datagram_length < 6:
                raise ValueError('Long header is too short (%d bytes)' % datagram_length)

            version, cid_lengths = unpack_from('!LB', data, 1)
            pos = 6

            destination_cid_length = decode_cid_length(cid_lengths // 16)
            destination_cid = data[pos:pos + destination_cid_length]
            pos += destination_cid_length

            source_cid_length = decode_cid_length(cid_lengths % 16)
            source_cid = data[pos:pos + source_cid_length]
            pos += source_cid_length

            packet_type = first_byte & PACKET_TYPE_MASK
            if packet_type == PACKET_TYPE_INITIAL:
                token_length, pos = unpack_variable_length(data, pos)
                token = data[pos:pos + token_length]
                pos += token_length

                length, pos = unpack_variable_length(data, pos)

            return QuicHeader(
                version=version,
                destination_cid=destination_cid,
                source_cid=source_cid,
                encrypted_offset=pos,
                token=token)
        else:
            # short header packet
            raise ValueError('Short header is not supported yet')
