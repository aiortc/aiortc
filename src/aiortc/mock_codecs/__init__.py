
from ..rtcrtpparameters import (
    RTCRtpCapabilities,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionParameters,
    RTCRtpCodecCapability,
    RTCRtpHeaderExtensionCapability,
)
from .base import Encoder, Decoder

def depayload(codec: RTCRtpCodecParameters, payload: bytes) -> bytes:
    # if codec.name == "VP8":
    #     return vp8_depayload(payload)
    # elif codec.name == "H264":
    #     return h264_depayload(payload)
    # else:
    return payload


def get_capabilities(kind: str) -> RTCRtpCapabilities:
    if kind not in CODECS:
        raise ValueError(f"cannot get capabilities for unknown media {kind}")

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

    if mimeType == "audio/g722":
        return G722Decoder()
    elif mimeType == "audio/opus":
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
        raise ValueError(f"No decoder found for MIME type `{mimeType}`")


def get_encoder(codec: RTCRtpCodecParameters) -> Encoder:
    mimeType = codec.mimeType.lower()

    if mimeType == "audio/g722":
        return G722Encoder()
    elif mimeType == "audio/opus":
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
        raise ValueError(f"No encoder found for MIME type `{mimeType}`")
        
# The clockrate for G.722 is 8kHz even though the sampling rate is 16kHz.
# See https://datatracker.ietf.org/doc/html/rfc3551
G722_CODEC = RTCRtpCodecParameters(
    mimeType="audio/G722", clockRate=8000, channels=1, payloadType=9
)
PCMU_CODEC = RTCRtpCodecParameters(
    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
)
PCMA_CODEC = RTCRtpCodecParameters(
    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
)

CODECS: dict[str, list[RTCRtpCodecParameters]] = {
    "audio": [
        RTCRtpCodecParameters(
            mimeType="audio/opus", clockRate=48000, channels=2, payloadType=96
        ),
        G722_CODEC,
        PCMU_CODEC,
        PCMA_CODEC,
    ],
    "video": [],
}
# Note, the id space for these extensions is shared across media types when BUNDLE
# is negotiated. If you add a audio- or video-specific extension, make sure it has
# a unique id.
HEADER_EXTENSIONS: dict[str, list[RTCRtpHeaderExtensionParameters]] = {
    "audio": [
        RTCRtpHeaderExtensionParameters(
            id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
        ),
        RTCRtpHeaderExtensionParameters(
            id=2, uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
        ),
    ],
    "video": [
        RTCRtpHeaderExtensionParameters(
            id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
        ),
        RTCRtpHeaderExtensionParameters(
            id=3, uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"
        ),
    ],
}


def is_rtx(codec: Union[RTCRtpCodecCapability, RTCRtpCodecParameters]) -> bool:
    return codec.name.lower() == "rtx"