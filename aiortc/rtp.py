from struct import pack, unpack

# reserved to avoid confusion with RTCP
FORBIDDEN_PAYLOAD_TYPES = range(72, 77)
DYNAMIC_PAYLOAD_TYPES = range(96, 128)


class Codec:
    def __init__(self, kind, name, clockrate, channels=None, pt=None):
        self.kind = kind
        self.name = name
        self.clockrate = clockrate
        self.channels = channels
        self.pt = pt

    def clone(self, pt):
        return Codec(kind=self.kind, name=self.name, clockrate=self.clockrate,
                     channels=self.channels, pt=pt)

    def __str__(self):
        return '%s/%d' % (self.name, self.clockrate)


class Packet:
    def __init__(self, payload_type, extension=0, marker=0, sequence_number=0, timestamp=0, ssrc=0):
        self.version = 2
        self.extension = extension
        self.marker = marker
        self.payload_type = payload_type
        self.sequence_number = sequence_number
        self.timestamp = timestamp
        self.ssrc = ssrc
        self.csrc = []
        self.payload = b''

    def __bytes__(self):
        data = pack(
            '!BBHLL',
            (self.version << 6) | len(self.csrc),
            (self.marker << 7) | self.payload_type,
            self.sequence_number,
            self.timestamp,
            self.ssrc)
        for csrc in self.csrc:
            data += pack('!L', csrc)
        return data + self.payload

    @classmethod
    def parse(cls, data):
        if len(data) < 12:
            raise ValueError('RTP packet length is less than 12 bytes')

        v_x_cc, m_pt, sequence_number, timestamp, ssrc = unpack('!BBHLL', data[0:12])
        version = (v_x_cc >> 6)
        cc = (v_x_cc & 0x0f)
        if version != 2:
            raise ValueError('RTP packet has invalid version')

        packet = cls(
            marker=(m_pt >> 7),
            payload_type=(m_pt & 0x7f),
            sequence_number=sequence_number,
            timestamp=timestamp,
            ssrc=ssrc)

        pos = 12
        for i in range(0, cc):
            packet.csrc.append(unpack('!L', data[pos:pos+4])[0])
            pos += 4

        packet.payload = data[pos:]
        return packet
