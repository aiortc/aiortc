from collections import OrderedDict
from typing import Dict, List, Optional, Union

from ..rtcrtpparameters import (
    RTCRtcpFeedback,
    RTCRtpCapabilities,
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionCapability,
    RTCRtpHeaderExtensionParameters,
)

try:
    from .base import Decoder, Encoder
    from .g711 import PcmaDecoder, PcmaEncoder, PcmuDecoder, PcmuEncoder
    from .h264 import H264Decoder, H264Encoder, h264_depayload
    from .opus import OpusDecoder, OpusEncoder
    from .vpx import Vp8Decoder, Vp8Encoder, vp8_depayload
except ImportError:
    class Decoder: pass
    class Encoder: pass

    NO_CODECS = True

PCMU_CODEC = RTCRtpCodecParameters(
    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
)
PCMA_CODEC = RTCRtpCodecParameters(
    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
)

CODECS = {
    "audio": [
        RTCRtpCodecParameters(
            mimeType="audio/opus", clockRate=48000, channels=2, payloadType=96
        ),
        PCMU_CODEC,
        PCMA_CODEC,
    ],
    "video": [],
}  # type: Dict[str, List[RTCRtpCodecParameters]]
HEADER_EXTENSIONS = {
    "audio": [
        RTCRtpHeaderExtensionParameters(id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid")
    ],
    "video": [
        RTCRtpHeaderExtensionParameters(
            id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
        ),
        RTCRtpHeaderExtensionParameters(
            id=2, uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"
        ),
    ],
}  # type: Dict[str, List[RTCRtpHeaderExtensionParameters]]


def init_codecs() -> None:
    dynamic_pt = 97

    def add_video_codec(
        mimeType: str, parameters: Optional[OrderedDict] = None
    ) -> None:
        nonlocal dynamic_pt

        clockRate = 90000
        CODECS["video"] += [
            RTCRtpCodecParameters(
                mimeType=mimeType,
                clockRate=clockRate,
                payloadType=dynamic_pt,
                rtcpFeedback=[
                    RTCRtcpFeedback(type="nack"),
                    RTCRtcpFeedback(type="nack", parameter="pli"),
                    RTCRtcpFeedback(type="goog-remb"),
                ],
                parameters=parameters or OrderedDict(),
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=clockRate,
                payloadType=dynamic_pt + 1,
                parameters=OrderedDict([("apt", dynamic_pt)]),
            ),
        ]
        dynamic_pt += 2

    add_video_codec("video/VP8")
    add_video_codec(
        "video/H264",
        OrderedDict(
            (
                ("packetization-mode", "1"),
                ("level-asymmetry-allowed", "1"),
                ("profile-level-id", "42001f"),
            )
        ),
    )
    add_video_codec(
        "video/H264",
        OrderedDict(
            (
                ("packetization-mode", "1"),
                ("level-asymmetry-allowed", "1"),
                ("profile-level-id", "42e01f"),
            )
        ),
    )


def depayload(codec: RTCRtpCodecParameters, payload: bytes) -> bytes:
    if codec.name == "VP8":
        return vp8_depayload(payload)
    elif codec.name == "H264":
        return h264_depayload(payload)
    else:
        return payload


def get_capabilities(kind: str) -> RTCRtpCapabilities:
    if kind not in CODECS:
        raise ValueError("cannot get capabilities for unknown media %s" % kind)

    codecs = []
    rtx_added = False
    for params in CODECS[kind]:
        if not is_rtx(params):
            codecs.append(
                RTCRtpCodecCapability(
                    mimeType=params.mimeType,
                    clockRate=params.clockRate,
                    channels=params.channels,
                    parameters=params.parameters,
                )
            )
        elif not rtx_added:
            # There will only be a single entry in codecs[] for retransmission
            # via RTX, with sdpFmtpLine not present.
            codecs.append(
                RTCRtpCodecCapability(
                    mimeType=params.mimeType, clockRate=params.clockRate
                )
            )
            rtx_added = True

    headerExtensions = []
    for extension in HEADER_EXTENSIONS[kind]:
        headerExtensions.append(RTCRtpHeaderExtensionCapability(uri=extension.uri))
    return RTCRtpCapabilities(codecs=codecs, headerExtensions=headerExtensions)


def get_decoder(codec: RTCRtpCodecParameters) -> Decoder:
    mimeType = codec.mimeType.lower()

    if mimeType == "audio/opus":
        return OpusDecoder()
    elif mimeType == "audio/pcma":
        return PcmaDecoder()
    elif mimeType == "audio/pcmu":
        return PcmuDecoder()
    elif mimeType == "video/h264":
        return H264Decoder()
    elif mimeType == "video/vp8":
        return Vp8Decoder()
    else:
        raise ValueError("No decoder found for MIME type `%s`" % mimeType)


def get_encoder(codec: RTCRtpCodecParameters) -> Encoder:
    mimeType = codec.mimeType.lower()

    if mimeType == "audio/opus":
        return OpusEncoder()
    elif mimeType == "audio/pcma":
        return PcmaEncoder()
    elif mimeType == "audio/pcmu":
        return PcmuEncoder()
    elif mimeType == "video/h264":
        return H264Encoder()
    elif mimeType == "video/vp8":
        return Vp8Encoder()
    else:
        raise ValueError("No encoder found for MIME type `%s`" % mimeType)


def is_rtx(codec: Union[RTCRtpCodecCapability, RTCRtpCodecParameters]) -> bool:
    return codec.name.lower() == "rtx"


if not NO_CODECS:
    init_codecs()
else:
    CODECS = {
        "audio": [],
        "video": [],
    }
