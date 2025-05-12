from dataclasses import dataclass, field
from typing import Optional, Union

ParametersDict = dict[str, Union[int, str, None]]


@dataclass
class RTCRtpCodecCapability:
    """
    The :class:`RTCRtpCodecCapability` dictionary provides information on
    codec capabilities.
    """

    mimeType: str
    "The codec MIME media type/subtype, for instance `'audio/PCMU'`."
    clockRate: int
    "The codec clock rate expressed in Hertz."
    channels: Optional[int] = None
    "The number of channels supported (e.g. two for stereo)."
    parameters: ParametersDict = field(default_factory=dict)
    "Codec-specific parameters available for signaling."

    @property
    def name(self) -> str:
        return self.mimeType.split("/")[1]


@dataclass
class RTCRtpCodecParameters:
    """
    The :class:`RTCRtpCodecParameters` dictionary provides information on
    codec settings.
    """

    mimeType: str
    "The codec MIME media type/subtype, for instance `'audio/PCMU'`."
    clockRate: int
    "The codec clock rate expressed in Hertz."
    channels: Optional[int] = None
    "The number of channels supported (e.g. two for stereo)."
    payloadType: Optional[int] = None
    "The value that goes in the RTP Payload Type Field."
    rtcpFeedback: list["RTCRtcpFeedback"] = field(default_factory=list)
    "Transport layer and codec-specific feedback messages for this codec."
    parameters: ParametersDict = field(default_factory=dict)
    "Codec-specific parameters available for signaling."

    @property
    def name(self) -> str:
        return self.mimeType.split("/")[1]

    def __str__(self) -> str:
        s = f"{self.name}/{self.clockRate}"
        if self.channels == 2:
            s += "/2"
        return s


@dataclass
class RTCRtpRtxParameters:
    ssrc: int


@dataclass
class RTCRtpCodingParameters:
    ssrc: int
    payloadType: int
    rtx: Optional[RTCRtpRtxParameters] = None


class RTCRtpDecodingParameters(RTCRtpCodingParameters):
    pass


class RTCRtpEncodingParameters(RTCRtpCodingParameters):
    pass


@dataclass
class RTCRtpHeaderExtensionCapability:
    """
    The :class:`RTCRtpHeaderExtensionCapability` dictionary provides information
    on a supported header extension.
    """

    uri: str
    "The URI of the RTP header extension."


@dataclass
class RTCRtpHeaderExtensionParameters:
    """
    The :class:`RTCRtpHeaderExtensionParameters` dictionary enables a header
    extension to be configured for use within an :class:`RTCRtpSender` or
    :class:`RTCRtpReceiver`.
    """

    id: int
    "The value that goes in the packet."
    uri: str
    "The URI of the RTP header extension."


@dataclass
class RTCRtpCapabilities:
    """
    The :class:`RTCRtpCapabilities` dictionary provides information about
    support codecs and header extensions.
    """

    codecs: list[RTCRtpCodecCapability] = field(default_factory=list)
    "A list of :class:`RTCRtpCodecCapability`."
    headerExtensions: list[RTCRtpHeaderExtensionCapability] = field(
        default_factory=list
    )
    "A list of :class:`RTCRtpHeaderExtensionCapability`."


@dataclass
class RTCRtcpFeedback:
    """
    The :class:`RTCRtcpFeedback` dictionary provides information on RTCP feedback
    messages.
    """

    type: str
    parameter: Optional[str] = None


@dataclass
class RTCRtcpParameters:
    """
    The :class:`RTCRtcpParameters` dictionary provides information on RTCP settings.
    """

    cname: Optional[str] = None
    "The Canonical Name (CNAME) used by RTCP."
    mux: bool = False
    "Whether RTP and RTCP are multiplexed."
    ssrc: Optional[int] = None
    "The Synchronization Source identifier."


@dataclass
class RTCRtpParameters:
    """
    The :class:`RTCRtpParameters` dictionary describes the configuration of
    an :class:`RTCRtpReceiver` or an :class:`RTCRtpSender`.
    """

    codecs: list[RTCRtpCodecParameters] = field(default_factory=list)
    "A list of :class:`RTCRtpCodecParameters` to send or receive."
    headerExtensions: list[RTCRtpHeaderExtensionParameters] = field(
        default_factory=list
    )
    "A list of :class:`RTCRtpHeaderExtensionParameters`."
    muxId: str = ""
    "The muxId assigned to the RTP stream, if any, empty string if unset."
    rtcp: RTCRtcpParameters = field(default_factory=RTCRtcpParameters)
    "Parameters to configure RTCP."


@dataclass
class RTCRtpReceiveParameters(RTCRtpParameters):
    encodings: list[RTCRtpDecodingParameters] = field(default_factory=list)


@dataclass
class RTCRtpSendParameters(RTCRtpParameters):
    encodings: list[RTCRtpEncodingParameters] = field(default_factory=list)
