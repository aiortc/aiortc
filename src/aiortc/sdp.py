import enum
import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

from . import rtp
from .rtcdtlstransport import RTCDtlsFingerprint, RTCDtlsParameters
from .rtcicetransport import RTCIceCandidate, RTCIceParameters
from .rtcrtpparameters import (
    ParametersDict,
    RTCRtcpFeedback,
    RTCRtpCodecParameters,
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
    bits = split_attr("candidate", sdp, minimum=8, maximum=None)

    candidate = RTCIceCandidate(
        # The component ID is 1 to 256 (RFC 8445 section 5.1.2.1).
        component=int_from_sdp("candidate attribute", bits[1], minimum=1, maximum=256),
        foundation=bits[0],
        ip=bits[4],
        port=port_from_sdp(bits[5]),
        # The priority is 1 to 2**31 - 1 (RFC 8445 section 5.1.2).
        priority=int_from_sdp(
            "candidate attribute", bits[3], minimum=1, maximum=2**31 - 1
        ),
        protocol=bits[2],
        type=bits[7],
    )

    for i in range(8, len(bits) - 1, 2):
        if bits[i] == "raddr":
            candidate.relatedAddress = bits[i + 1]
        elif bits[i] == "rport":
            candidate.relatedPort = port_from_sdp(bits[i + 1])
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


def dtls_role_from_sdp(sdp: str) -> str:
    # The "holdconn" value MUST NOT be used with DTLS (RFC 8842 section 5.1).
    if sdp not in DTLS_SETUP_ROLE:
        raise ValueError(f"Invalid setup attribute: {sdp}")
    return DTLS_SETUP_ROLE[sdp]


def fingerprint_from_sdp(sdp: str) -> RTCDtlsFingerprint:
    algorithm, value = split_attr("fingerprint", sdp, minimum=2, maximum=2)
    # Hex bytes separated by colons (RFC 8122 section 5), in either case as
    # the comparison with the certificate digest ignores case.
    if not re.fullmatch("[0-9A-F]{2}(:[0-9A-F]{2})*", value, re.I):
        raise ValueError(f"Invalid fingerprint attribute: {sdp}")
    return RTCDtlsFingerprint(algorithm=algorithm, value=value)


def grouplines(sdp: str) -> tuple[list[str], list[list[str]]]:
    session = []
    media = []
    for line in sdp.splitlines():
        # Be lenient with trailing whitespace, which no value we parse ends with.
        line = line.rstrip()
        if line.startswith("m="):
            media.append([line])
        elif len(media):
            media[-1].append(line)
        else:
            session.append(line)
    return session, media


def int_from_sdp(name: str, value: str, *, minimum: int, maximum: Optional[int]) -> int:
    """
    Parse a decimal integer between `minimum` and `maximum` inclusive, where
    a `maximum` of `None` means there is no upper bound.
    """
    # Unlike int(), reject signs, underscores, whitespace and non-ASCII numerals.
    if re.fullmatch("[0-9]+", value):
        number = int(value)
        if number >= minimum and (maximum is None or number <= maximum):
            return number
    raise ValueError(f"Invalid {name}: {value}")


def ipaddress_from_sdp(sdp: str) -> str:
    m = re.match("^IN (IP4|IP6) ([^ ]+)$", sdp)
    if not m:
        raise ValueError(f"Invalid connection address: {sdp}")
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
                parameters[k] = int_from_sdp(
                    "fmtp attribute", v, minimum=0, maximum=None
                )
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


def parse_attr(line: str) -> tuple[str, str]:
    # A property attribute has no value (RFC 8866 section 5.13).
    attr, _, value = line[2:].partition(":")
    return attr, value


def parse_h264_profile_level_id(profile_str: str) -> tuple[H264Profile, H264Level]:
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


def payload_type_from_sdp(sdp: str) -> int:
    # RTP payload types are 7 bits (RFC 3550 section 5.1).
    return int_from_sdp("payload type", sdp, minimum=0, maximum=127)


def port_from_sdp(sdp: str) -> int:
    return int_from_sdp("port", sdp, minimum=0, maximum=65535)


def split_attr(
    attr: str,
    value: str,
    *,
    minimum: int,
    maximum: Optional[int],
    sep: Optional[str] = None,
) -> list[str]:
    """
    Split an attribute value into at most `maximum` fields, the last keeping
    the rest of the value, and require at least `minimum` fields. A `maximum`
    of `None` splits the whole value.
    """
    bits = value.split(sep, -1 if maximum is None else maximum - 1)
    if len(bits) < minimum:
        raise ValueError(f"Invalid {attr} attribute: {value}")
    return bits


def ssrc_from_sdp(sdp: str) -> int:
    # SSRCs are 32 bits (RFC 3550 section 5.1).
    return int_from_sdp("SSRC", sdp, minimum=0, maximum=2**32 - 1)


@dataclass
class ExtmapDescription:
    id: int
    uri: str
    direction: Optional[str] = None
    attributes: Optional[str] = None

    def __str__(self) -> str:
        value = str(self.id)
        if self.direction is not None:
            value += f"/{self.direction}"
        value += f" {self.uri}"
        if self.attributes is not None:
            value += f" {self.attributes}"
        return value


@dataclass
class GroupDescription:
    semantic: str
    items: list[Union[int, str]]

    def __str__(self) -> str:
        return f"{self.semantic} {' '.join(map(str, self.items))}"


def parse_group(
    dest: list[GroupDescription],
    value: str,
    parse_item: Callable[[str], Union[int, str]] = str,
) -> None:
    bits = value.split()
    if bits:
        dest.append(
            GroupDescription(semantic=bits[0], items=list(map(parse_item, bits[1:])))
        )


@dataclass
class SsrcDescription:
    ssrc: int
    cname: Optional[str] = None
    msid: Optional[str] = None
    mslabel: Optional[str] = None
    label: Optional[str] = None


SSRC_INFO_ATTRS = ["cname", "msid", "mslabel", "label"]


class MediaDescription:
    def __init__(self, kind: str, port: int, profile: str, fmt: list[Any]) -> None:
        # rtp
        self.kind = kind
        self.port = port
        self.host: Optional[str] = None
        self.profile = profile
        self.bundle_only = False
        self.direction: Optional[str] = None
        self.extmap: list[ExtmapDescription] = []
        self.msid: Optional[str] = None

        # rtcp
        self.rtcp_port: Optional[int] = None
        self.rtcp_host: Optional[str] = None
        self.rtcp_mux = False

        # ssrc
        self.ssrc: list[SsrcDescription] = []
        self.ssrc_group: list[GroupDescription] = []

        # formats
        self.fmt = fmt
        self.rtp = RTCRtpParameters()

        # SCTP
        self.sctpCapabilities: Optional[RTCSctpCapabilities] = None
        self.sctpmap: dict[int, str] = {}
        self.sctp_port: Optional[int] = None

        # DTLS
        self.dtls: Optional[RTCDtlsParameters] = None

        # ICE
        self.ice: Optional[RTCIceParameters] = None
        self.ice_candidates: list[RTCIceCandidate] = []
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

        for extmap in self.extmap:
            lines.append(f"a=extmap:{extmap}")

        if self.rtp.muxId:
            lines.append(f"a=mid:{self.rtp.muxId}")
        if self.bundle_only:
            lines.append("a=bundle-only")

        if self.msid:
            lines.append(f"a=msid:{self.msid}")

        if self.rtcp_port is not None:
            line = f"a=rtcp:{self.rtcp_port}"
            if self.rtcp_host is not None:
                line += f" {ipaddress_to_sdp(self.rtcp_host)}"
            lines.append(line)
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
        self.group: list[GroupDescription] = []
        self.msid_semantic: list[GroupDescription] = []
        self.media: list[MediaDescription] = []
        self.type: Optional[str] = None

    @classmethod
    def parse(cls, sdp: str) -> "SessionDescription":
        current_media: Optional[MediaDescription] = None
        dtls_fingerprints = []
        dtls_role = None
        ice_lite = False
        ice_options = None
        ice_password = None
        ice_usernameFragment = None

        def find_codec(pt: int) -> Optional[RTCRtpCodecParameters]:
            return next(
                filter(lambda x: x.payloadType == pt, current_media.rtp.codecs), None
            )

        session_lines, media_groups = grouplines(sdp)

        # parse session
        session = cls()
        for line in session_lines:
            if line.startswith("v="):
                # The version MUST be 0 (RFC 9429 section 5.8.1).
                session.version = int_from_sdp(
                    "version", line.strip()[2:], minimum=0, maximum=0
                )
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
                    dtls_fingerprints.append(fingerprint_from_sdp(value))
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
                    dtls_role = dtls_role_from_sdp(value)

        # parse media
        for media_lines in media_groups:
            m = re.match("^m=([^ ]+) ([0-9]+) ([A-Z/]+) (.+)$", media_lines[0])
            if not m:
                raise ValueError(f"Invalid media description: {media_lines[0][2:]}")

            # check payload types are valid
            kind = m.group(1)
            fmt = m.group(4).split()
            fmt_int: Optional[list[int]] = None
            if kind in ["audio", "video"]:
                fmt_int = []
                for value in fmt:
                    pt = payload_type_from_sdp(value)
                    # Some payload types conflict with RTCP (RFC 5761 section 4).
                    if pt in rtp.FORBIDDEN_PAYLOAD_TYPES:
                        raise ValueError(f"Invalid payload type: {value}")
                    fmt_int.append(pt)

            current_media = MediaDescription(
                kind=kind,
                port=port_from_sdp(m.group(2)),
                profile=m.group(3),
                fmt=fmt_int or fmt,
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
                    if attr == "bundle-only":
                        current_media.bundle_only = True
                    elif attr == "candidate":
                        current_media.ice_candidates.append(candidate_from_sdp(value))
                    elif attr == "end-of-candidates":
                        current_media.ice_candidates_complete = True
                    elif attr == "extmap":
                        # The URI may be followed by extension attributes, whose
                        # meaning depends on the extension (RFC 8285 section 8).
                        ext_id, ext_uri, *ext_rest = split_attr(
                            attr, value, minimum=2, maximum=3
                        )
                        ext_attributes = ext_rest[0] if ext_rest else None
                        ext_direction = None
                        if "/" in ext_id:
                            ext_id, ext_direction = ext_id.split("/")
                        # IDs are 1 to 256, or 4096 to 4351 to offer alternatives
                        # (RFC 8285 sections 4.3 and 7).
                        ext_id_int = int_from_sdp(
                            "extmap attribute", ext_id, minimum=1, maximum=4351
                        )
                        if 256 < ext_id_int < 4096:
                            raise ValueError(f"Invalid extmap attribute: {ext_id}")
                        current_media.extmap.append(
                            ExtmapDescription(
                                id=ext_id_int,
                                uri=ext_uri,
                                direction=ext_direction,
                                attributes=ext_attributes,
                            )
                        )
                    elif attr == "fingerprint":
                        current_media.dtls.fingerprints.append(
                            fingerprint_from_sdp(value)
                        )
                    elif attr == "ice-options":
                        current_media.ice_options = value
                    elif attr == "ice-pwd":
                        current_media.ice.password = value
                    elif attr == "ice-ufrag":
                        current_media.ice.usernameFragment = value
                    elif attr == "max-message-size":
                        current_media.sctpCapabilities = RTCSctpCapabilities(
                            maxMessageSize=int_from_sdp(
                                "max-message-size attribute",
                                value,
                                minimum=0,
                                maximum=None,
                            )
                        )
                    elif attr == "mid":
                        current_media.rtp.muxId = value
                    elif attr == "msid":
                        current_media.msid = value
                    elif attr == "rtcp":
                        bits = split_attr(attr, value, minimum=1, maximum=2)
                        current_media.rtcp_port = port_from_sdp(bits[0])
                        if len(bits) > 1:
                            current_media.rtcp_host = ipaddress_from_sdp(bits[1])
                    elif attr == "rtcp-mux":
                        current_media.rtcp_mux = True
                    elif attr == "setup":
                        current_media.dtls.role = dtls_role_from_sdp(value)
                    elif attr in DIRECTIONS:
                        current_media.direction = attr
                    elif attr == "rtpmap" and fmt_int is not None:
                        format_id, format_desc = split_attr(
                            attr, value, minimum=2, maximum=2
                        )
                        bits = split_attr(
                            attr, format_desc, minimum=2, maximum=3, sep="/"
                        )
                        if current_media.kind == "audio":
                            if len(bits) > 2:
                                channels = int_from_sdp(
                                    "rtpmap attribute", bits[2], minimum=1, maximum=None
                                )
                            else:
                                channels = 1
                        else:
                            channels = None
                        codec = RTCRtpCodecParameters(
                            mimeType=current_media.kind + "/" + bits[0],
                            channels=channels,
                            # A zero clock rate would divide by zero in jitter
                            # calculations.
                            clockRate=int_from_sdp(
                                "rtpmap attribute", bits[1], minimum=1, maximum=None
                            ),
                            payloadType=payload_type_from_sdp(format_id),
                        )
                        current_media.rtp.codecs.append(codec)
                    elif attr == "sctpmap":
                        format_id, format_desc = split_attr(
                            attr, value, minimum=2, maximum=2
                        )
                        current_media.sctpmap[port_from_sdp(format_id)] = format_desc
                    elif attr == "sctp-port":
                        current_media.sctp_port = port_from_sdp(value)
                    elif attr == "ssrc-group":
                        parse_group(current_media.ssrc_group, value, ssrc_from_sdp)
                    elif attr == "ssrc":
                        ssrc_str, ssrc_desc = split_attr(
                            attr, value, minimum=2, maximum=2
                        )
                        ssrc = ssrc_from_sdp(ssrc_str)
                        # A source-level attribute may be a flag, without a value
                        # (RFC 5576 section 4.1).
                        ssrc_attr, colon, ssrc_value = ssrc_desc.partition(":")

                        try:
                            ssrc_info = next(
                                (x for x in current_media.ssrc if x.ssrc == ssrc)
                            )
                        except StopIteration:
                            ssrc_info = SsrcDescription(ssrc=ssrc)
                            current_media.ssrc.append(ssrc_info)
                        if colon and ssrc_attr in SSRC_INFO_ATTRS:
                            setattr(ssrc_info, ssrc_attr, ssrc_value)

            # Keep only the formats on the "m=" line, in its order of preference
            # (RFC 8866 sections 5.14 and 6.6), as "a=" lines may come in any order.
            if fmt_int is not None:
                position: dict[int, int] = {}
                for index, pt in enumerate(fmt_int):
                    position.setdefault(pt, index)
                current_media.rtp.codecs = sorted(
                    (c for c in current_media.rtp.codecs if c.payloadType in position),
                    key=lambda c: position[c.payloadType],
                )

            if current_media.dtls.role is None:
                current_media.dtls = None

            # Apply parameters and feedback once all codecs have been parsed.
            for line in media_lines[1:]:
                if line.startswith("a="):
                    attr, value = parse_attr(line)
                    # Formats are payload types only in RTP media (RFC 8866
                    # section 5.14).
                    if attr == "fmtp" and fmt_int is not None:
                        format_id, format_desc = split_attr(
                            attr, value, minimum=2, maximum=2
                        )
                        # Ignore parameters for a format with no codec, such as
                        # one missing from the "m=" line or without "a=rtpmap".
                        codec = find_codec(payload_type_from_sdp(format_id))
                        if codec is not None:
                            codec.parameters = parameters_from_sdp(format_desc)
                    elif attr == "rtcp-fb":
                        bits = split_attr(attr, value, minimum=2, maximum=3)
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
            msid_semantic = self.msid_semantic
            if not msid_semantic:
                # assume "WMS *" for backward compat.
                msid_semantic = [GroupDescription(semantic="WMS", items=["*"])]
            for group in msid_semantic:
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
