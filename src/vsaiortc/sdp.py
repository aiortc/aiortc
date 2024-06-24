import enum
import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from . import rtp
from .rtcdtlstransport import RTCDtlsFingerprint, RTCDtlsParameters
from .rtcicetransport import RTCIceCandidate, RTCIceParameters
from .rtcrtpparameters import (
    ParametersDict,
    RTCRtcpFeedback,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionParameters,
    RTCRtpParameters,
)
from .rtcsctptransport import RTCSctpCapabilities

DIRECTIONS = ["inactive", "sendonly", "recvonly", "sendrecv"]

DTLS_ROLE_SETUP = {"auto": "actpass", "client": "active", "server": "passive"}
DTLS_SETUP_ROLE = dict([(v, k) for (k, v) in DTLS_ROLE_SETUP.items()])

FMTP_INT_PARAMETERS = [
    "apt",
    "max-fr",
    "max-fs",
    "maxplaybackrate",
    "minptime",
    "stereo",
    "useinbandfec",
]


class BitPattern:
    def __init__(self, v: str) -> None:
        self._mask = ~self._bytemaskstring("x", v)
        self._masked_value = self._bytemaskstring("1", v)

    def matches(self, v: int) -> bool:
        return (v & self._mask) == self._masked_value

    def _bytemaskstring(self, c: str, s: str) -> int:
        return (
            (s[0] == c) << 7
            | (s[1] == c) << 6
            | (s[2] == c) << 5
            | (s[3] == c) << 4
            | (s[4] == c) << 3
            | (s[5] == c) << 2
            | (s[6] == c) << 1
            | (s[7] == c) << 0
        )


class H264Profile(enum.Enum):
    PROFILE_CONSTRAINED_BASELINE = 0
    PROFILE_BASELINE = 1
    PROFILE_MAIN = 2
    PROFILE_CONSTRAINED_HIGH = 3
    PROFILE_HIGH = 4
    PROFILE_PREDICTIVE_HIGH_444 = 5


class H264Level(enum.IntEnum):
    LEVEL1_B = -1
    LEVEL1 = 10
    LEVEL1_1 = 11
    LEVEL1_2 = 12
    LEVEL1_3 = 13
    LEVEL2 = 20
    LEVEL2_1 = 21
    LEVEL2_2 = 22
    LEVEL3 = 30
    LEVEL3_1 = 31
    LEVEL3_2 = 32
    LEVEL4 = 40
    LEVEL4_1 = 41
    LEVEL4_2 = 42
    LEVEL5 = 50
    LEVEL5_1 = 51
    LEVEL5_2 = 52


H264_PROFILE_PATTERNS = [
    (0x42, BitPattern("x1xx0000"), H264Profile.PROFILE_CONSTRAINED_BASELINE),
    (0x4D, BitPattern("1xxx0000"), H264Profile.PROFILE_CONSTRAINED_BASELINE),
    (0x58, BitPattern("11xx0000"), H264Profile.PROFILE_CONSTRAINED_BASELINE),
    (0x42, BitPattern("x0xx0000"), H264Profile.PROFILE_BASELINE),
    (0x58, BitPattern("10xx0000"), H264Profile.PROFILE_BASELINE),
    (0x4D, BitPattern("0x0x0000"), H264Profile.PROFILE_MAIN),
    (0x64, BitPattern("00000000"), H264Profile.PROFILE_HIGH),
    (0x64, BitPattern("00001100"), H264Profile.PROFILE_CONSTRAINED_HIGH),
    (0xF4, BitPattern("00000000"), H264Profile.PROFILE_PREDICTIVE_HIGH_444),
]


def candidate_from_sdp(sdp: str) -> RTCIceCandidate:
    bits = sdp.split()
    assert len(bits) >= 8

    candidate = RTCIceCandidate(
        component=int(bits[1]),
        foundation=bits[0],
        ip=bits[4],
        port=int(bits[5]),
        priority=int(bits[3]),
        protocol=bits[2],
        type=bits[7],
    )

    for i in range(8, len(bits) - 1, 2):
        if bits[i] == "raddr":
            candidate.relatedAddress = bits[i + 1]
        elif bits[i] == "rport":
            candidate.relatedPort = int(bits[i + 1])
        elif bits[i] == "tcptype":
            candidate.tcpType = bits[i + 1]

    return candidate


def candidate_to_sdp(candidate: RTCIceCandidate) -> str:
    sdp = (
        f"{candidate.foundation} {candidate.component} {candidate.protocol} "
        f"{candidate.priority} {candidate.ip} {candidate.port} typ {candidate.type}"
    )

    if candidate.relatedAddress is not None:
        sdp += f" raddr {candidate.relatedAddress}"
    if candidate.relatedPort is not None:
        sdp += f" rport {candidate.relatedPort}"
    if candidate.tcpType is not None:
        sdp += f" tcptype {candidate.tcpType}"
    return sdp


def grouplines(sdp: str) -> Tuple[List[str], List[List[str]]]:
    session = []
    media = []
    for line in sdp.splitlines():
        if line.startswith("m="):
            media.append([line])
        elif len(media):
            media[-1].append(line)
        else:
            session.append(line)
    return session, media


def ipaddress_from_sdp(sdp: str) -> str:
    m = re.match("^IN (IP4|IP6) ([^ ]+)$", sdp)
    assert m
    return m.group(2)


def ipaddress_to_sdp(addr: str) -> str:
    version = ipaddress.ip_address(addr).version
    return f"IN IP{version} {addr}"


def parameters_from_sdp(sdp: str) -> ParametersDict:
    parameters: ParametersDict = {}
    for param in sdp.split(";"):
        if "=" in param:
            k, v = param.split("=", 1)
            if k in FMTP_INT_PARAMETERS:
                parameters[k] = int(v)
            else:
                parameters[k] = v
        else:
            parameters[param] = None
    return parameters


def parameters_to_sdp(parameters: ParametersDict) -> str:
    params = []
    for param_k, param_v in parameters.items():
        if param_v is not None:
            params.append(f"{param_k}={param_v}")
        else:
            params.append(param_k)
    return ";".join(params)


def parse_attr(line: str) -> Tuple[str, Optional[str]]:
    if ":" in line:
        bits = line[2:].split(":", 1)
        return bits[0], bits[1]
    else:
        return line[2:], None


def parse_h264_profile_level_id(profile_str: str) -> Tuple[H264Profile, H264Level]:
    if not isinstance(profile_str, str) or not re.match(
        "[0-9a-f]{6}", profile_str, re.I
    ):
        raise ValueError("Expected a 6 character hexadecimal string")

    level_idc = int(profile_str[4:6], 16)
    profile_iop = int(profile_str[2:4], 16)
    profile_idc = int(profile_str[0:2], 16)

    level: H264Level
    if level_idc == H264Level.LEVEL1_1:
        level = H264Level.LEVEL1_B if (profile_iop & 0x10) else H264Level.LEVEL1_1
    else:
        level = H264Level(level_idc)

    for idc, pattern, profile in H264_PROFILE_PATTERNS:
        if idc == profile_idc and pattern.matches(profile_iop):
            return profile, level

    raise ValueError(
        f"Unrecognized profile_iop = {profile_iop}, profile_idc = {profile_idc}"
    )


@dataclass
class GroupDescription:
    semantic: str
    items: List[Union[int, str]]

    def __str__(self) -> str:
        return f"{self.semantic} {' '.join(map(str, self.items))}"


def parse_group(dest: List[GroupDescription], value: str, type=str) -> None:
    bits = value.split()
    if bits:
        dest.append(GroupDescription(semantic=bits[0], items=list(map(type, bits[1:]))))


@dataclass
class SsrcDescription:
    ssrc: int
    cname: Optional[str] = None
    msid: Optional[str] = None
    mslabel: Optional[str] = None
    label: Optional[str] = None


SSRC_INFO_ATTRS = ["cname", "msid", "mslabel", "label"]


class MediaDescription:
    def __init__(self, kind: str, port: int, profile: str, fmt: List[Any]) -> None:
        # rtp
        self.kind = kind
        self.port = port
        self.host: Optional[str] = None
        self.profile = profile
        self.direction: Optional[str] = None
        self.msid: Optional[str] = None

        # rtcp
        self.rtcp_port: Optional[int] = None
        self.rtcp_host: Optional[str] = None
        self.rtcp_mux = False

        # ssrc
        self.ssrc: List[SsrcDescription] = []
        self.ssrc_group: List[GroupDescription] = []

        # formats
        self.fmt = fmt
        self.rtp = RTCRtpParameters()

        # SCTP
        self.sctpCapabilities: Optional[RTCSctpCapabilities] = None
        self.sctpmap: Dict[int, str] = {}
        self.sctp_port: Optional[int] = None

        # DTLS
        self.dtls: Optional[RTCDtlsParameters] = None

        # ICE
        self.ice: Optional[RTCIceParameters] = None
        self.ice_candidates: List[RTCIceCandidate] = []
        self.ice_candidates_complete = False
        self.ice_options: Optional[str] = None

    def __str__(self) -> str:
        lines = []
        lines.append(
            f"m={self.kind} {self.port} {self.profile} {' '.join(map(str, self.fmt))}"
        )
        if self.host is not None:
            lines.append(f"c={ipaddress_to_sdp(self.host)}")
        if self.direction is not None:
            lines.append(f"a={self.direction}")

        for header in self.rtp.headerExtensions:
            lines.append(f"a=extmap:{header.id} {header.uri}")

        if self.rtp.muxId:
            lines.append(f"a=mid:{self.rtp.muxId}")

        if self.msid:
            lines.append(f"a=msid:{self.msid}")

        if self.rtcp_port is not None and self.rtcp_host is not None:
            lines.append(f"a=rtcp:{self.rtcp_port} {ipaddress_to_sdp(self.rtcp_host)}")
            if self.rtcp_mux:
                lines.append("a=rtcp-mux")

        for group in self.ssrc_group:
            lines.append(f"a=ssrc-group:{group}")
        for ssrc_info in self.ssrc:
            for ssrc_attr in SSRC_INFO_ATTRS:
                ssrc_value = getattr(ssrc_info, ssrc_attr)
                if ssrc_value is not None:
                    lines.append(f"a=ssrc:{ssrc_info.ssrc} {ssrc_attr}:{ssrc_value}")

        for codec in self.rtp.codecs:
            lines.append(f"a=rtpmap:{codec.payloadType} {codec}")

            # RTCP feedback
            for feedback in codec.rtcpFeedback:
                value = feedback.type
                if feedback.parameter:
                    value += f" {feedback.parameter}"
                lines.append(f"a=rtcp-fb:{codec.payloadType} {value}")

            # parameters
            params = parameters_to_sdp(codec.parameters)
            if params:
                lines.append(f"a=fmtp:{codec.payloadType} {params}")

        for k, v in self.sctpmap.items():
            lines.append(f"a=sctpmap:{k} {v}")
        if self.sctp_port is not None:
            lines.append(f"a=sctp-port:{self.sctp_port}")
        if self.sctpCapabilities is not None:
            lines.append(f"a=max-message-size:{self.sctpCapabilities.maxMessageSize}")

        # ice
        for candidate in self.ice_candidates:
            lines.append("a=candidate:" + candidate_to_sdp(candidate))
        if self.ice_candidates_complete:
            lines.append("a=end-of-candidates")
        if self.ice.usernameFragment is not None:
            lines.append(f"a=ice-ufrag:{self.ice.usernameFragment}")
        if self.ice.password is not None:
            lines.append(f"a=ice-pwd:{self.ice.password}")
        if self.ice_options is not None:
            lines.append(f"a=ice-options:{self.ice_options}")

        # dtls
        if self.dtls:
            for fingerprint in self.dtls.fingerprints:
                lines.append(
                    f"a=fingerprint:{fingerprint.algorithm} {fingerprint.value}"
                )
            lines.append(f"a=setup:{DTLS_ROLE_SETUP[self.dtls.role]}")

        return "\r\n".join(lines) + "\r\n"


class SessionDescription:
    def __init__(self) -> None:
        self.version = 0
        self.origin: Optional[str] = None
        self.name = "-"
        self.time = "0 0"
        self.host: Optional[str] = None
        self.group: List[GroupDescription] = []
        self.msid_semantic: List[GroupDescription] = []
        self.media: List[MediaDescription] = []
        self.type: Optional[str] = None

    @classmethod
    def parse(cls, sdp: str):
        current_media: Optional[MediaDescription] = None
        dtls_fingerprints = []
        dtls_role = None
        ice_lite = False
        ice_options = None
        ice_password = None
        ice_usernameFragment = None

        def find_codec(pt: int) -> RTCRtpCodecParameters:
            return next(filter(lambda x: x.payloadType == pt, current_media.rtp.codecs))

        session_lines, media_groups = grouplines(sdp)

        # parse session
        session = cls()
        for line in session_lines:
            if line.startswith("v="):
                session.version = int(line.strip()[2:])
            elif line.startswith("o="):
                session.origin = line.strip()[2:]
            elif line.startswith("s="):
                session.name = line.strip()[2:]
            elif line.startswith("c="):
                session.host = ipaddress_from_sdp(line[2:])
            elif line.startswith("t="):
                session.time = line.strip()[2:]
            elif line.startswith("a="):
                attr, value = parse_attr(line)
                if attr == "fingerprint":
                    algorithm, fingerprint = value.split()
                    dtls_fingerprints.append(
                        RTCDtlsFingerprint(algorithm=algorithm, value=fingerprint)
                    )
                elif attr == "ice-lite":
                    ice_lite = True
                elif attr == "ice-options":
                    ice_options = value
                elif attr == "ice-pwd":
                    ice_password = value
                elif attr == "ice-ufrag":
                    ice_usernameFragment = value
                elif attr == "group":
                    parse_group(session.group, value)
                elif attr == "msid-semantic":
                    parse_group(session.msid_semantic, value)
                elif attr == "setup":
                    dtls_role = DTLS_SETUP_ROLE[value]

        # parse media
        for media_lines in media_groups:
            m = re.match("^m=([^ ]+) ([0-9]+) ([A-Z/]+) (.+)$", media_lines[0])
            assert m

            # check payload types are valid
            kind = m.group(1)
            fmt = m.group(4).split()
            fmt_int: Optional[List[int]] = None
            if kind in ["audio", "video"]:
                fmt_int = [int(x) for x in fmt]
                for pt in fmt_int:
                    assert pt >= 0 and pt < 256
                    assert pt not in rtp.FORBIDDEN_PAYLOAD_TYPES

            current_media = MediaDescription(
                kind=kind, port=int(m.group(2)), profile=m.group(3), fmt=fmt_int or fmt
            )
            current_media.dtls = RTCDtlsParameters(
                fingerprints=dtls_fingerprints[:], role=dtls_role
            )
            current_media.ice = RTCIceParameters(
                iceLite=ice_lite,
                usernameFragment=ice_usernameFragment,
                password=ice_password,
            )
            current_media.ice_options = ice_options
            session.media.append(current_media)

            for line in media_lines[1:]:
                if line.startswith("c="):
                    current_media.host = ipaddress_from_sdp(line[2:])
                elif line.startswith("a="):
                    attr, value = parse_attr(line)
                    if attr == "candidate":
                        current_media.ice_candidates.append(candidate_from_sdp(value))
                    elif attr == "end-of-candidates":
                        current_media.ice_candidates_complete = True
                    elif attr == "extmap":
                        ext_id, ext_uri = value.split()
                        if "/" in ext_id:
                            ext_id, ext_direction = ext_id.split("/")
                        extension = RTCRtpHeaderExtensionParameters(
                            id=int(ext_id), uri=ext_uri
                        )
                        current_media.rtp.headerExtensions.append(extension)
                    elif attr == "fingerprint":
                        algorithm, fingerprint = value.split()
                        current_media.dtls.fingerprints.append(
                            RTCDtlsFingerprint(algorithm=algorithm, value=fingerprint)
                        )
                    elif attr == "ice-options":
                        current_media.ice_options = value
                    elif attr == "ice-pwd":
                        current_media.ice.password = value
                    elif attr == "ice-ufrag":
                        current_media.ice.usernameFragment = value
                    elif attr == "max-message-size":
                        current_media.sctpCapabilities = RTCSctpCapabilities(
                            maxMessageSize=int(value)
                        )
                    elif attr == "mid":
                        current_media.rtp.muxId = value
                    elif attr == "msid":
                        current_media.msid = value
                    elif attr == "rtcp":
                        port, rest = value.split(" ", 1)
                        current_media.rtcp_port = int(port)
                        current_media.rtcp_host = ipaddress_from_sdp(rest)
                    elif attr == "rtcp-mux":
                        current_media.rtcp_mux = True
                    elif attr == "setup":
                        current_media.dtls.role = DTLS_SETUP_ROLE[value]
                    elif attr in DIRECTIONS:
                        current_media.direction = attr
                    elif attr == "rtpmap":
                        format_id, format_desc = value.split(" ", 1)
                        bits = format_desc.split("/")
                        if current_media.kind == "audio":
                            if len(bits) > 2:
                                channels = int(bits[2])
                            else:
                                channels = 1
                        else:
                            channels = None
                        codec = RTCRtpCodecParameters(
                            mimeType=current_media.kind + "/" + bits[0],
                            channels=channels,
                            clockRate=int(bits[1]),
                            payloadType=int(format_id),
                        )
                        current_media.rtp.codecs.append(codec)
                    elif attr == "sctpmap":
                        format_id, format_desc = value.split(" ", 1)
                        getattr(current_media, attr)[int(format_id)] = format_desc
                    elif attr == "sctp-port":
                        current_media.sctp_port = int(value)
                    elif attr == "ssrc-group":
                        parse_group(current_media.ssrc_group, value, type=int)
                    elif attr == "ssrc":
                        ssrc_str, ssrc_desc = value.split(" ", 1)
                        ssrc = int(ssrc_str)
                        ssrc_attr, ssrc_value = ssrc_desc.split(":", 1)

                        try:
                            ssrc_info = next(
                                (x for x in current_media.ssrc if x.ssrc == ssrc)
                            )
                        except StopIteration:
                            ssrc_info = SsrcDescription(ssrc=ssrc)
                            current_media.ssrc.append(ssrc_info)
                        if ssrc_attr in SSRC_INFO_ATTRS:
                            setattr(ssrc_info, ssrc_attr, ssrc_value)

            if current_media.dtls.role is None:
                current_media.dtls = None

            # requires codecs to have been parsed
            for line in media_lines[1:]:
                if line.startswith("a="):
                    attr, value = parse_attr(line)
                    if attr == "fmtp":
                        format_id, format_desc = value.split(" ", 1)
                        codec = find_codec(int(format_id))
                        codec.parameters = parameters_from_sdp(format_desc)
                    elif attr == "rtcp-fb":
                        bits = value.split(" ", 2)
                        for codec in current_media.rtp.codecs:
                            if bits[0] in ["*", str(codec.payloadType)]:
                                codec.rtcpFeedback.append(
                                    RTCRtcpFeedback(
                                        type=bits[1],
                                        parameter=bits[2] if len(bits) > 2 else None,
                                    )
                                )

        return session

    def webrtc_track_id(self, media: MediaDescription) -> Optional[str]:
        assert media in self.media
        if media.msid is not None and " " in media.msid:
            bits = media.msid.split()
            for group in self.msid_semantic:
                if group.semantic == "WMS" and (
                    bits[0] in group.items or "*" in group.items
                ):
                    return bits[1]
        return None

    def __str__(self) -> str:
        lines = [f"v={self.version}", f"o={self.origin}", f"s={self.name}"]
        if self.host is not None:
            lines += [f"c={ipaddress_to_sdp(self.host)}"]
        lines += [f"t={self.time}"]
        if any(m.ice.iceLite for m in self.media):
            lines += ["a=ice-lite"]
        for group in self.group:
            lines += [f"a=group:{group}"]
        for group in self.msid_semantic:
            lines += [f"a=msid-semantic:{group}"]
        return "\r\n".join(lines) + "\r\n" + "".join([str(m) for m in self.media])
