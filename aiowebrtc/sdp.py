import re

import aioice


class ParsedMedia:
    def __init__(self, type, port, dtls_fingerprint=None, ice_ufrag=None, ice_pwd=None):
        self.type = type
        self.port = port
        self.dtls_fingerprint = dtls_fingerprint
        self.ice_candidates = []
        self.ice_ufrag = ice_ufrag
        self.ice_pwd = ice_pwd


class ParsedDescription:
    def __init__(self, sdp):
        self.media = []

        current_media = None
        dtls_fingerprint = None

        for line in sdp.splitlines():
            if line.startswith('m='):
                m = re.match('^m=([^ ]+) ([0-9]+) ([A-Z/]+) (.+)', line)
                assert m
                current_media = ParsedMedia(
                    type=m.group(1), port=int(m.group(2)),
                    dtls_fingerprint=dtls_fingerprint)
                self.media.append(current_media)

            if line.startswith('a=') and ':' in line:
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
