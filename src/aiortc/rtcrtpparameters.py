from collections import OrderedDict
from typing import List  # noqa

import attr


@attr.s
class RTCRtpCodecCapability:
    """
    The :class:`RTCRtpCodecCapability` dictionary provides information on
    codec capabilities.
    """

    mimeType: str = attr.ib()
    "The codec MIME media type/subtype, for instance `'audio/PCMU'`."
    clockRate: int = attr.ib()
    "The codec clock rate expressed in Hertz."
    channels: int = attr.ib(default=None)
    "The number of channels supported (e.g. two for stereo)."
    parameters: OrderedDict = attr.ib(default=attr.Factory(OrderedDict))
    "Codec-specific parameters available for signaling."

    @property
    def name(self):
        return self.mimeType.split("/")[1]


@attr.s
class RTCRtpCodecParameters:
    """
    The :class:`RTCRtpCodecParameters` dictionary provides information on
    codec settings.
    """

    mimeType: str = attr.ib()
    "The codec MIME media type/subtype, for instance `'audio/PCMU'`."
    clockRate: int = attr.ib()
    "The codec clock rate expressed in Hertz."
    channels: int = attr.ib(default=None)
    "The number of channels supported (e.g. two for stereo)."
    payloadType: int = attr.ib(default=None)
    "The value that goes in the RTP Payload Type Field."
    rtcpFeedback: List["RTCRtcpFeedback"] = attr.ib(default=attr.Factory(list))
    "Transport layer and codec-specific feedback messages for this codec."
    parameters: OrderedDict = attr.ib(default=attr.Factory(OrderedDict))
    "Codec-specific parameters available for signaling."

    @property
    def name(self):
        return self.mimeType.split("/")[1]

    def __str__(self):
        s = f"{self.name}/{self.clockRate}"
        if self.channels == 2:
            s += "/2"
        return s


@attr.s
class RTCRtpRtxParameters:
    ssrc: int = attr.ib()


@attr.s
class RTCRtpCodingParameters:
    ssrc: int = attr.ib()
    payloadType: int = attr.ib()
    rtx: RTCRtpRtxParameters = attr.ib(default=None)


@attr.s
class RTCRtpDecodingParameters(RTCRtpCodingParameters):
    pass


@attr.s
class RTCRtpEncodingParameters(RTCRtpCodingParameters):
    pass


@attr.s
class RTCRtpHeaderExtensionCapability:
    """
    The :class:`RTCRtpHeaderExtensionCapability` dictionary provides information
    on a supported header extension.
    """

    uri: str = attr.ib()
    "The URI of the RTP header extension."


@attr.s
class RTCRtpHeaderExtensionParameters:
    """
    The :class:`RTCRtpHeaderExtensionParameters` dictionary enables a header
    extension to be configured for use within an :class:`RTCRtpSender` or
    :class:`RTCRtpReceiver`.
    """

    id: int = attr.ib()
    "The value that goes in the packet."
    uri: str = attr.ib()
    "The URI of the RTP header extension."


@attr.s
class RTCRtpCapabilities:
    """
    The :class:`RTCRtpCapabilities` dictionary provides information about
    support codecs and header extensions.
    """

    codecs: List[RTCRtpCodecCapability] = attr.ib(default=attr.Factory(list))
    "A list of :class:`RTCRtpCodecCapability`."
    headerExtensions: List[RTCRtpHeaderExtensionCapability] = attr.ib(
        default=attr.Factory(list)
    )
    "A list of :class:`RTCRtpHeaderExtensionCapability`."


@attr.s
class RTCRtcpFeedback:
    """
    The :class:`RTCRtcpFeedback` dictionary provides information on RTCP feedback messages.
    """

    type: str = attr.ib()
    parameter: str = attr.ib(default=None)


@attr.s
class RTCRtcpParameters:
    """
    The :class:`RTCRtcpParameters` dictionary provides information on RTCP settings.
    """

    cname: str = attr.ib(default=None)
    "The Canonical Name (CNAME) used by RTCP."
    mux: bool = attr.ib(default=False)
    "Whether RTP and RTCP are multiplexed."
    ssrc: int = attr.ib(default=None)
    "The Synchronization Source identifier."


@attr.s
class RTCRtpParameters:
    """
    The :class:`RTCRtpParameters` dictionary describes the configuration of
    an :class:`RTCRtpReceiver` or an :class:`RTCRtpSender`.
    """

    codecs: List[RTCRtpCodecParameters] = attr.ib(default=attr.Factory(list))
    "A list of :class:`RTCRtpCodecParameters` to send or receive."
    headerExtensions: List[RTCRtpHeaderExtensionParameters] = attr.ib(
        default=attr.Factory(list)
    )
    "A list of :class:`RTCRtpHeaderExtensionParameters`."
    muxId: str = attr.ib(default="")
    "The muxId assigned to the RTP stream, if any, empty string if unset."
    rtcp: RTCRtcpParameters = attr.ib(default=attr.Factory(RTCRtcpParameters))
    "Parameters to configure RTCP."


@attr.s
class RTCRtpReceiveParameters(RTCRtpParameters):
    encodings: List[RTCRtpDecodingParameters] = attr.ib(default=attr.Factory(list))


@attr.s
class RTCRtpSendParameters(RTCRtpParameters):
    encodings: List[RTCRtpEncodingParameters] = attr.ib(default=attr.Factory(list))
