from struct import pack, unpack

# reserved to avoid confusion with RTCP
FORBIDDEN_PAYLOAD_TYPES = range(72, 77)
DYNAMIC_PAYLOAD_TYPES = range(96, 128)

RTCP_SR = 200
RTCP_RR = 201
RTCP_SDES = 202
RTCP_BYE = 203


def is_rtcp(msg):
    return len(msg) >= 2 and msg[1] >= 192 and msg[1] <= 208


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
        s = '%s/%d' % (self.name, self.clockrate)
        if self.channels == 2:
            s += '/2'
        return s


class RtcpPacket:
    def __init__(self, packet_type, ssrc):
        self.version = 2
        self.packet_type = packet_type
        self.ssrc = ssrc

    @classmethod
    def parse(cls, data):
        if len(data) < 8:
            raise ValueError('RTCP packet length is less than 8 bytes')

        v_p_rc, packet_type, length, ssrc = unpack('!BBHL', data[0:8])
        version = (v_p_rc >> 6)
        if version != 2:
            raise ValueError('RTCP packet has invalid version')

        return cls(packet_type=packet_type, ssrc=ssrc)


class RtpPacket:
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
            (self.version << 6) | (self.extension << 4) | len(self.csrc),
            (self.marker << 7) | self.payload_type,
            self.sequence_number,
            self.timestamp,
            self.ssrc)
        for csrc in self.csrc:
            data += pack('!L', csrc)
        return data + self.payload

    def __repr__(self):
        return 'RtpPacket(seq=%d, ts=%s, marker=%d, payload=%d, %d bytes)' % (
            self.sequence_number, self.timestamp, self.marker, self.payload_type, len(self.payload))

    @classmethod
    def parse(cls, data):
        if len(data) < 12:
            raise ValueError('RTP packet length is less than 12 bytes')

        v_p_x_cc, m_pt, sequence_number, timestamp, ssrc = unpack('!BBHLL', data[0:12])
        version = (v_p_x_cc >> 6)
        padding = ((v_p_x_cc >> 5) & 1)
        cc = (v_p_x_cc & 0x0f)
        if version != 2:
            raise ValueError('RTP packet has invalid version')

        packet = cls(
            extension=((v_p_x_cc >> 4) & 1),
            marker=(m_pt >> 7),
            payload_type=(m_pt & 0x7f),
            sequence_number=sequence_number,
            timestamp=timestamp,
            ssrc=ssrc)

        pos = 12
        for i in range(0, cc):
            packet.csrc.append(unpack('!L', data[pos:pos+4])[0])
            pos += 4

        if padding:
            padding_len = data[-1]
            if not padding_len or padding_len > len(data) - pos:
                raise ValueError('RTP packet padding length is invalid')
            packet.payload = data[pos:-padding_len]
        else:
            packet.payload = data[pos:]

        return packet
