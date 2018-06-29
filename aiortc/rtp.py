import datetime
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

NTP_EPOCH = datetime.datetime(1900, 1, 1, tzinfo=datetime.timezone.utc)


def pack_rtcp_packet(packet_type, count, payload):
    assert len(payload) % 4 == 0
    return pack('!BBH',
                (2 << 6) | count,
                packet_type,
                len(payload) // 4) + payload


def datetime_from_ntp(ntp):
    seconds = (ntp >> 32)
    microseconds = ((ntp & 0xffffffff) * 1000000) / (1 << 32)
    return NTP_EPOCH + datetime.timedelta(seconds=seconds, microseconds=microseconds)


def datetime_to_ntp(dt):
    delta = dt - NTP_EPOCH
    high = int(delta.total_seconds())
    low = round((delta.microseconds * (1 << 32)) // 1000000)
    return (high << 32) | low


def is_rtcp(msg):
    return len(msg) >= 2 and msg[1] >= 192 and msg[1] <= 208


def seq_gt(a, b):
    half_mod = (1 << 15)
    return (((a < b) and ((b - a) > half_mod)) or
            ((a > b) and ((a - b) < half_mod)))


def seq_plus_one(a):
    return (a + 1) % RTP_SEQ_MODULO


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


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


@attr.s
class RtcpSourceInfo:
    ssrc = attr.ib()
    items = attr.ib()


class RtcpPacket:
    @classmethod
    def parse(cls, data):
        pos = 0
        packets = []

        while pos < len(data):
            if len(data) < RTCP_HEADER_LENGTH:
                raise ValueError('RTCP packet length is less than %d bytes' % RTCP_HEADER_LENGTH)

            v_p_count, packet_type, length = unpack('!BBH', data[pos:pos + 4])
            version = (v_p_count >> 6)
            # padding = ((v_p_count >> 5) & 1)
            count = (v_p_count & 0x1f)
            if version != 2:
                raise ValueError('RTCP packet has invalid version')
            pos += 4
            end = pos + length * 4
            payload = data[pos:end]
            pos = end

            if packet_type == RTCP_BYE:
                packets.append(RtcpByePacket.parse(payload, count))
            elif packet_type == RTCP_SDES:
                packets.append(RtcpSdesPacket.parse(payload, count))
            elif packet_type == RTCP_SR:
                packets.append(RtcpSrPacket.parse(payload, count))
            elif packet_type == RTCP_RR:
                packets.append(RtcpRrPacket.parse(payload, count))

        return packets


@attr.s
class RtcpByePacket:
    sources = attr.ib()

    def __bytes__(self):
        payload = b''.join([pack('!L', ssrc) for ssrc in self.sources])
        return pack_rtcp_packet(RTCP_BYE, len(self.sources), payload)

    @classmethod
    def parse(cls, data, count):
        sources = list(unpack('!' + ('L' * count), data))
        return cls(sources=sources)


@attr.s
class RtcpRrPacket:
    ssrc = attr.ib()
    reports = attr.ib(default=attr.Factory(list))

    def __bytes__(self):
        payload = pack('!L', self.ssrc)
        for report in self.reports:
            payload += bytes(report)
        return pack_rtcp_packet(RTCP_RR, len(self.reports), payload)

    @classmethod
    def parse(cls, data, count):
        ssrc = unpack('!L', data[0:4])[0]
        pos = 4
        reports = []
        for r in range(count):
            reports.append(RtcpReceiverInfo.parse(data[pos:pos + 24]))
            pos += 24
        return cls(ssrc=ssrc, reports=reports)


@attr.s
class RtcpSdesPacket:
    chunks = attr.ib(default=attr.Factory(list))

    def __bytes__(self):
        payload = b''
        for chunk in self.chunks:
            payload += pack('!L', chunk.ssrc)
            for d_type, d_value in chunk.items:
                payload += pack('!BB', d_type, len(d_value)) + d_value
            payload += b'\x00\x00'
        while len(payload) % 4:
            payload += b'\x00'
        return pack_rtcp_packet(RTCP_SDES, len(self.chunks), payload)

    @classmethod
    def parse(cls, data, count):
        pos = 0
        chunks = []
        for r in range(count):
            ssrc = unpack('!L', data[pos:pos + 4])[0]
            pos += 4
            items = []
            while True:
                d_type, d_length = unpack('!BB', data[pos:pos + 2])
                pos += 2
                d_value = data[pos:pos + d_length]
                pos += d_length
                if d_type == 0:
                    break
                else:
                    items.append((d_type, d_value))
            chunks.append(RtcpSourceInfo(ssrc=ssrc, items=items))
        return cls(chunks=chunks)


@attr.s
class RtcpSrPacket:
    ssrc = attr.ib()
    sender_info = attr.ib()
    reports = attr.ib(default=attr.Factory(list))

    def __bytes__(self):
        payload = pack('!L', self.ssrc)
        payload += bytes(self.sender_info)
        for report in self.reports:
            payload += bytes(report)
        return pack_rtcp_packet(RTCP_SR, len(self.reports), payload)

    @classmethod
    def parse(cls, data, count):
        ssrc = unpack('!L', data[0:4])[0]
        sender_info = RtcpSenderInfo.parse(data[4:24])
        pos = 24
        reports = []
        for r in range(count):
            reports.append(RtcpReceiverInfo.parse(data[pos:pos + 24]))
            pos += 24
        return RtcpSrPacket(ssrc=ssrc, sender_info=sender_info, reports=reports)


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
