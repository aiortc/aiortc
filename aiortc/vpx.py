import struct


class VpxPayloadDescriptor:
    props = ['partition_start', 'partition_id', 'picture_id']

    def __init__(self, partition_start, partition_id, picture_id=None):
        self.partition_start = partition_start
        self.partition_id = partition_id
        self.picture_id = picture_id

    def __bytes__(self):
        octet = (self.partition_start << 4) | self.partition_id
        if self.picture_id is not None:
            ext_octet = 1 << 7
            data = struct.pack('!BB', (1 << 7) | octet, ext_octet)
            if self.picture_id < 128:
                data += struct.pack('!B', self.picture_id)
            else:
                data += struct.pack('!H', (1 << 15) | self.picture_id)
            return data
        else:
            return struct.pack('!B', octet)

    def __repr__(self):
        return 'VpxPayloadDescriptor(S=%d, PID=%d, pic_id=%s)' % (
            self.partition_start, self.partition_id, self.picture_id)

    @classmethod
    def parse(cls, data):
        # first byte
        octet = data[0]
        extended = octet >> 7
        partition_start = (octet >> 4) & 1
        partition_id = octet & 0xf
        picture_id = None
        pos = 1

        # extended control bits
        if extended:
            octet = data[pos]
            ext_I = (octet >> 7) & 1
            ext_L = (octet >> 6) & 1
            ext_T = (octet >> 5) & 1
            ext_K = (octet >> 4) & 1
            pos += 1

            # picture id
            if ext_I:
                if data[pos] & 0x80:
                    picture_id = struct.unpack('!H', data[pos:pos+2])[0] & 0x7fff
                    pos += 2
                else:
                    picture_id = data[pos]
                    pos += 1

            # unused
            if ext_L:
                pos += 1
            if ext_T or ext_K:
                pos += 1

        obj = cls(partition_start=partition_start, partition_id=partition_id, picture_id=picture_id)
        return obj, data[pos:]
