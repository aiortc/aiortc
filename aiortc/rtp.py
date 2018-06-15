from struct import pack, unpack

import attr

# reserved to avoid confusion with RTCP
FORBIDDEN_PAYLOAD_TYPES = range(72, 77)
DYNAMIC_PAYLOAD_TYPES = range(96, 128)

RTP_HEADER_LENGTH = 12
RTP_SEQ_MODULO = 2 ** 16
RTCP_HEADER_LENGTH = 8

RTCP_SR = 200
RTCP_RR = 201
RTCP_SDES = 202
RTCP_BYE = 203


def is_rtcp(msg):
    return len(msg) >= 2 and msg[1] >= 192 and msg[1] <= 208


def seq_plus_one(a):
    return (a + 1) % RTP_SEQ_MODULO


@attr.s
class RtcpReceiverInfo:
    ssrc = attr.ib()
    fraction_lost = attr.ib()
    packets_lost = attr.ib()
    highest_sequence = attr.ib()
    jitter = attr.ib()
    lsr = attr.ib()
    dlsr = attr.ib()

    def __bytes__(self):
        lost = (self.fraction_lost << 24) | self.packets_lost
        return pack('!LLLLLL', self.ssrc, lost, self.highest_sequence,
                    self.jitter, self.lsr, self.dlsr)

    @classmethod
    def parse(cls, data):
        ssrc, lost, highest_sequence, jitter, lsr, dlsr = unpack('!LLLLLL', data)
        return cls(
            ssrc=ssrc,
            fraction_lost=(lost >> 24) & 0xff,
            packets_lost=lost & 0xffffff,
            highest_sequence=highest_sequence,
            jitter=jitter,
            lsr=lsr,
            dlsr=dlsr
        )


@attr.s
class RtcpSenderInfo:
    ntp_timestamp = attr.ib()
    rtp_timestamp = attr.ib()
    packet_count = attr.ib()
    octet_count = attr.ib()

    def __bytes__(self):
        return pack('!QLLL',
                    self.ntp_timestamp,
                    self.rtp_timestamp,
                    self.packet_count,
                    self.octet_count)

    @classmethod
    def parse(cls, data):
        ntp_timestamp, rtp_timestamp, packet_count, octet_count = unpack('!QLLL', data)
        return cls(
            ntp_timestamp=ntp_timestamp,
            rtp_timestamp=rtp_timestamp,
            packet_count=packet_count,
            octet_count=octet_count)


class RtcpPacket:
    def __init__(self, packet_type, ssrc):
        self.version = 2
        self.packet_type = packet_type
        self.ssrc = ssrc
        self.reports = []
        self.extension = b''

    def __bytes__(self):
        data = pack('!BBHL',
                    (self.version << 6) | len(self.reports),
                    self.packet_type,
                    self._length,
                    self.ssrc)

        if self.packet_type == RTCP_SR:
            data += bytes(self.sender_info)

        for report in self.reports:
            data += bytes(report)

        data += self.extension

        return data

    def __repr__(self):
        return 'RtcpPacket(pt=%d)' % self.packet_type

    @classmethod
    def parse(cls, data):
        pos = 0
        packets = []

        while pos < len(data):
            start = pos

            if len(data) < RTCP_HEADER_LENGTH:
                raise ValueError('RTCP packet length is less than %d bytes' % RTCP_HEADER_LENGTH)

            v_p_count, packet_type, length, ssrc = unpack('!BBHL', data[pos:pos + 8])
            version = (v_p_count >> 6)
            # padding = ((v_p_rc >> 5) & 1)
            count = (v_p_count & 0x1f)
            if version != 2:
                raise ValueError('RTCP packet has invalid version')
            pos += 8

            p = cls(packet_type=packet_type, ssrc=ssrc)
            p._length = length
            if packet_type == RTCP_SR:
                p.sender_info = RtcpSenderInfo.parse(data[pos:pos + 20])
                pos += 20

            if packet_type in [RTCP_SR, RTCP_RR]:
                for r in range(count):
                    p.reports.append(RtcpReceiverInfo.parse(data[pos:pos + 24]))
                    pos += 24
            elif packet_type == RTCP_SDES:
                for r in range(count):
                    r_start = pos
                    while True:
                        d_type, d_length = unpack('!BB', data[pos:pos + 2])
                        pos += 2 + d_length
                        if d_type == 0:
                            break
                    p.reports.append(data[r_start:pos])

            end = start + (length + 1) * 4
            p.extension = data[pos:end]
            packets.append(p)
            pos = end

        return packets


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
        if len(data) < RTP_HEADER_LENGTH:
            raise ValueError('RTP packet length is less than %d bytes' % RTP_HEADER_LENGTH)

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
