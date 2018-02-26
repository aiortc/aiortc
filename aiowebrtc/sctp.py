import struct

import crcmod.predefined


crc32c = crcmod.predefined.mkPredefinedCrcFun('crc-32c')


def swapl(i):
    return struct.unpack("<I", struct.pack(">I", i))[0]


class Chunk:
    def __init__(self, type, flags, data):
        self.type = type
        self.flags = flags
        self.data = data

    def __bytes__(self):
        return struct.pack('!BBH', self.type, self.flags, len(self.data)) + self.data


class Packet:
    def __init__(self, source_port, destination_port, verification_tag):
        self.source_port = source_port
        self.destination_port = destination_port
        self.verification_tag = verification_tag
        self.chunks = []

    def __bytes__(self):
        checksum = 0
        data = struct.pack(
            '!HHII',
            self.source_port,
            self.destination_port,
            self.verification_tag,
            checksum)
        for chunk in self.chunks:
            data += bytes(chunk)
        return data

    @classmethod
    def parse(cls, data):
        source_port, destination_port, verification_tag, checksum = struct.unpack(
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
            chunk_type, chunk_flags, chunk_length = struct.unpack('!BBH', data[pos:pos + 4])
            pos += 4
            packet.chunks.append(Chunk(
                type=chunk_type,
                flags=chunk_flags,
                data=data[pos:pos + chunk_length]))
            pos += chunk_length
        return packet
