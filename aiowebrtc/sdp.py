import re

import aioice


class MediaDescription:
    def __init__(self, kind, port, profile, fmt):
        self.kind = kind
        self.port = port
        self.host = None
        self.profile = profile
        self.fmt = fmt

        # DTLS
        self.dtls_fingerprint = None

        # ICE
        self.ice_candidates = []
        self.ice_ufrag = None
        self.ice_pwd = None


class SessionDescription:
    def __init__(self):
        self.media = []

    @classmethod
    def parse(cls, sdp):
        current_media = None
        dtls_fingerprint = None
        session = cls()

        for line in sdp.splitlines():
            if line.startswith('m='):
                m = re.match('^m=([^ ]+) ([0-9]+) ([A-Z/]+) (.+)$', line)
                assert m
                current_media = MediaDescription(
                    kind=m.group(1),
                    port=int(m.group(2)),
                    profile=m.group(3),
                    fmt=[int(x) for x in m.group(4).split()])
                current_media.dtls_fingerprint = dtls_fingerprint
                session.media.append(current_media)
            elif line.startswith('c=') and current_media:
                m = re.match('^c=IN (IP4|IP6) ([^ ]+)$', line)
                assert m
                current_media.host = m.group(2)
            elif line.startswith('a=') and ':' in line:
                attr, value = line[2:].split(':', 1)
                if current_media:
                    if attr == 'candidate':
                        current_media.ice_candidates.append(aioice.Candidate.from_sdp(value))
                    elif attr == 'fingerprint':
                        algo, fingerprint = value.split()
                        assert algo == 'sha-256'
                        current_media.dtls_fingerprint = fingerprint
                    elif attr == 'ice-ufrag':
                        current_media.ice_ufrag = value
                    elif attr == 'ice-pwd':
                        current_media.ice_pwd = value
                else:
                    # session-level attributes
                    if attr == 'fingerprint':
                        algo, fingerprint = value.split()
                        assert algo == 'sha-256'
                        dtls_fingerprint = fingerprint

        return session
