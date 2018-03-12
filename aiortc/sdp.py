import ipaddress
import re

from . import rtp
from .rtcdtlstransport import RTCDtlsFingerprint, RTCDtlsParameters
from .rtcicetransport import RTCIceCandidate, RTCIceParameters
from .rtcrtpparameters import RTCRtpCodecParameters, RTCRtpParameters
from .rtcsctptransport import RTCSctpCapabilities

DIRECTIONS = [
    'sendrecv',
    'sendonly',
    'recvonly',
    'inactive'
]

DTLS_ROLE_SETUP = {
    'auto': 'actpass',
    'client': 'active',
    'server': 'passive'
}
DTLS_SETUP_ROLE = dict([(v, k) for (k, v) in DTLS_ROLE_SETUP.items()])


def ipaddress_from_sdp(sdp):
    m = re.match('^IN (IP4|IP6) ([^ ]+)$', sdp)
    assert m
    return m.group(2)


def ipaddress_to_sdp(addr):
    version = ipaddress.ip_address(addr).version
    return 'IN IP%d %s' % (version, addr)


class MediaDescription:
    def __init__(self, kind, port, profile, fmt):
        # rtp
        self.kind = kind
        self.port = port
        self.host = None
        self.profile = profile
        self.direction = None

        # rtcp
        self.rtcp_port = None
        self.rtcp_host = None
        self.rtcp_mux = False

        # formats
        self.fmt = fmt
        self.rtp = RTCRtpParameters()
        self.sctpmap = {}

        # SCTP
        self.sctpCapabilities = None

        # DTLS
        self.dtls = RTCDtlsParameters()

        # ICE
        self.ice = RTCIceParameters()
        self.ice_candidates = []

    def __str__(self):
        lines = []
        lines.append('m=%s %d %s %s' % (
            self.kind,
            self.port,
            self.profile,
            ' '.join(map(str, self.fmt))
        ))
        lines.append('c=%s' % ipaddress_to_sdp(self.host))
        if self.direction is not None:
            lines.append('a=' + self.direction)

        if self.rtcp_port is not None and self.rtcp_host is not None:
            lines.append('a=rtcp:%d %s' % (self.rtcp_port, ipaddress_to_sdp(self.rtcp_host)))
        if self.rtcp_mux:
            lines.append('a=rtcp-mux')

        for codec in self.rtp.codecs:
            lines.append('a=rtpmap:%d %s' % (codec.payloadType, codec))

        for k, v in self.sctpmap.items():
            lines.append('a=sctpmap:%d %s' % (k, v))
        if self.sctpCapabilities is not None:
            lines.append('a=max-message-size:%d' % self.sctpCapabilities.maxMessageSize)

        # ice
        for candidate in self.ice_candidates:
            lines.append('a=candidate:' + candidate.to_sdp())
        if self.ice.usernameFragment is not None:
            lines.append('a=ice-ufrag:' + self.ice.usernameFragment)
        if self.ice.password is not None:
            lines.append('a=ice-pwd:' + self.ice.password)

        # dtls
        for fingerprint in self.dtls.fingerprints:
            lines.append('a=fingerprint:%s %s' % (fingerprint.algorithm, fingerprint.value))
        lines.append('a=setup:' + DTLS_ROLE_SETUP[self.dtls.role])

        return '\r\n'.join(lines) + '\r\n'


class SessionDescription:
    def __init__(self):
        self.media = []

    @classmethod
    def parse(cls, sdp):
        current_media = None
        dtls_fingerprints = []
        session = cls()

        for line in sdp.splitlines():
            if line.startswith('m='):
                m = re.match('^m=([^ ]+) ([0-9]+) ([A-Z/]+) (.+)$', line)
                assert m

                # check payload types are valid
                kind = m.group(1)
                fmt = [int(x) for x in m.group(4).split()]
                if kind in ['audio', 'video']:
                    for pt in fmt:
                        assert pt >= 0 and pt < 256
                        assert pt not in rtp.FORBIDDEN_PAYLOAD_TYPES

                current_media = MediaDescription(
                    kind=kind,
                    port=int(m.group(2)),
                    profile=m.group(3),
                    fmt=fmt)
                current_media.dtls.fingerprints = dtls_fingerprints
                session.media.append(current_media)
            elif line.startswith('c=') and current_media:
                current_media.host = ipaddress_from_sdp(line[2:])
            elif line.startswith('a='):
                if ':' in line:
                    attr, value = line[2:].split(':', 1)
                else:
                    attr = line[2:]
                if current_media:
                    if attr == 'candidate':
                        current_media.ice_candidates.append(RTCIceCandidate.from_sdp(value))
                    elif attr == 'fingerprint':
                        algorithm, fingerprint = value.split()
                        current_media.dtls.fingerprints.append(RTCDtlsFingerprint(
                            algorithm=algorithm,
                            value=fingerprint))
                    elif attr == 'ice-ufrag':
                        current_media.ice.usernameFragment = value
                    elif attr == 'ice-pwd':
                        current_media.ice.password = value
                    elif attr == 'max-message-size':
                        current_media.sctpCapabilities = RTCSctpCapabilities(
                            maxMessageSize=int(value))
                    elif attr == 'rtcp':
                        port, rest = value.split(' ', 1)
                        current_media.rtcp_port = int(port)
                        current_media.rtcp_host = ipaddress_from_sdp(rest)
                    elif attr == 'rtcp-mux':
                        current_media.rtcp_mux = True
                    elif attr == 'setup':
                        current_media.dtls.role = DTLS_SETUP_ROLE[value]
                    elif attr in DIRECTIONS:
                        current_media.direction = attr
                    elif attr == 'rtpmap':
                        format_id, format_desc = value.split(' ', 1)
                        format_id = int(format_id)
                        bits = format_desc.split('/')
                        codec = RTCRtpCodecParameters(
                            name=bits[0],
                            channels=int(bits[2]) if len(bits) > 2 else None,
                            clockRate=int(bits[1]),
                            payloadType=int(format_id))
                        current_media.rtp.codecs.append(codec)
                    elif attr == 'sctpmap':
                        format_id, format_desc = value.split(' ', 1)
                        getattr(current_media, attr)[int(format_id)] = format_desc
                else:
                    # session-level attributes
                    if attr == 'fingerprint':
                        algorithm, fingerprint = value.split()
                        dtls_fingerprints.append(RTCDtlsFingerprint(
                            algorithm=algorithm,
                            value=fingerprint))

        return session
