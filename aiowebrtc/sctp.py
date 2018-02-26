import enum
from struct import pack, unpack

import crcmod.predefined


crc32c = crcmod.predefined.mkPredefinedCrcFun('crc-32c')


def decode_params(body):
    params = []
    pos = 0
    while pos < len(body) - 4:
        tag_type, tag_length = unpack('!HH', body[pos:pos + 4])
        params.append((tag_type, body[pos + 4:pos + tag_length]))
        pos += tag_length + padl(tag_length)
    return params


def encode_params(params):
    body = b''
    padding = b''
    for tag_type, tag_value in params:
        body += padding
        body += pack('!HH', tag_type, len(tag_value) + 4) + tag_value
        padding = b'\x00' * padl(len(tag_value))
    return body


def padl(l):
    return 4 * ((l + 3) // 4) - l


def swapl(i):
    return unpack("<I", pack(">I", i))[0]


class Chunk:
    def __bytes__(self):
        body = self.body
        data = pack('!BBH', self.type, self.flags, len(body) + 4) + body
        data += b'\x00' * padl(len(body))
        return data

class InitChunk(Chunk):
    def __init__(self, type, flags, body=None):
        self.type = type
        self.flags = flags
        self.params = []
        if body:
            (self.initiate_tag, self.advertise_rwnd, self.outbound_streams,
             self.inbound_streams, self.initial_tsn) = unpack('!LLHHL', body[0:16])
            self.params = decode_params(body[16:])
        else:
            self.params = []

    @property
    def body(self):
        body = pack(
            '!LLHHL', self.initiate_tag, self.advertise_rwnd, self.outbound_streams,
            self.inbound_streams, self.initial_tsn)
        body += encode_params(self.params)
        return body


class UnknownChunk(Chunk):
    def __init__(self, type, flags, body):
        self.type = type
        self.flags = flags
        self.body = body
        self.params = {}


class ChunkType(enum.IntEnum):
    INIT = 1


class Packet:
    def __init__(self, source_port, destination_port, verification_tag):
        self.source_port = source_port
        self.destination_port = destination_port
        self.verification_tag = verification_tag
        self.chunks = []

    def __bytes__(self):
        checksum = 0
        data = pack(
            '!HHII',
            self.source_port,
            self.destination_port,
            self.verification_tag,
            checksum)
        for chunk in self.chunks:
            data += bytes(chunk)

        # calculate checksum
        checksum = swapl(crc32c(data))
        return data[0:8] + pack('!I', checksum) + data[12:]

    @classmethod
    def parse(cls, data):
        source_port, destination_port, verification_tag, checksum = unpack(
            '!HHII', data[0:12])

        # verify checksum
        check_data = data[0:8] + b'\x00\x00\x00\x00' + data[12:]
        if checksum != swapl(crc32c(check_data)):
            raise ValueError('Invalid checksum')

        packet = cls(
            source_port=source_port,
            destination_port=destination_port,
            verification_tag=verification_tag)

        pos = 12
        while pos < len(data) - 4:
            chunk_type, chunk_flags, chunk_length = unpack('!BBH', data[pos:pos + 4])
            chunk_body = data[pos + 4:pos + chunk_length]
            if chunk_type == ChunkType.INIT:
                cls = InitChunk
            else:
                cls = UnknownChunk
            packet.chunks.append(cls(
                type=chunk_type,
                flags=chunk_flags,
                body=chunk_body))
            pos += chunk_length + padl(chunk_length)
        return packet


class Transport:
    def __init__(self, transport):
        self.transport = transport

    async def run(self):
        while True:
            data = await self.transport.recv()
            try:
                packet = Packet.parse(data)
            except ValueError:
                continue

            print('FROM port', packet.source_port, 'to', packet.destination_port)
