from collections import OrderedDict

from ..rtcrtpparameters import (RTCRtcpFeedback, RTCRtpCapabilities,
                                RTCRtpCodecCapability, RTCRtpCodecParameters,
                                RTCRtpHeaderExtensionCapability,
                                RTCRtpHeaderExtensionParameters)
from .g711 import PcmaDecoder, PcmaEncoder, PcmuDecoder, PcmuEncoder
from .h264 import H264Decoder, H264Encoder, h264_depayload
from .opus import OpusDecoder, OpusEncoder
from .vpx import Vp8Decoder, Vp8Encoder, vp8_depayload

PCMU_CODEC = RTCRtpCodecParameters(mimeType='audio/PCMU', clockRate=8000, channels=1, payloadType=0)
PCMA_CODEC = RTCRtpCodecParameters(mimeType='audio/PCMA', clockRate=8000, channels=1, payloadType=8)

CODECS = {
    'audio': [
        RTCRtpCodecParameters(mimeType='audio/opus', clockRate=48000, channels=2, payloadType=96),
        PCMU_CODEC,
        PCMA_CODEC,
    ],
    'video': [],
}
HEADER_EXTENSIONS = {
    'audio': [
        RTCRtpHeaderExtensionParameters(id=1, uri='urn:ietf:params:rtp-hdrext:sdes:mid'),
    ],
    'video': [
        RTCRtpHeaderExtensionParameters(id=1, uri='urn:ietf:params:rtp-hdrext:sdes:mid'),
        RTCRtpHeaderExtensionParameters(
            id=2, uri='http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time'),
    ]
}


def init_codecs():
    dynamic_pt = 97

    def add_video_codec(mimeType, parameters=None):
        nonlocal dynamic_pt

        clockRate = 90000
        CODECS['video'] += [
            RTCRtpCodecParameters(
                mimeType=mimeType,
                clockRate=clockRate,
                payloadType=dynamic_pt,
                rtcpFeedback=[
                    RTCRtcpFeedback(type='nack'),
                    RTCRtcpFeedback(type='nack', parameter='pli'),
                    RTCRtcpFeedback(type='goog-remb'),
                ],
                parameters=parameters or OrderedDict()),
            RTCRtpCodecParameters(
                mimeType='video/rtx',
                clockRate=clockRate,
                payloadType=dynamic_pt + 1,
                parameters={
                    'apt': dynamic_pt,
                })
        ]
        dynamic_pt += 2

    add_video_codec('video/VP8')
    add_video_codec('video/H264', OrderedDict((
        ('packetization-mode', '1'),
        ('level-asymmetry-allowed', '1'),
        ('profile-level-id', '42001f'),
    )))
    add_video_codec('video/H264', OrderedDict((
        ('packetization-mode', '1'),
        ('level-asymmetry-allowed', '1'),
        ('profile-level-id', '42e01f'),
    )))


def depayload(codec, payload):
    if codec.name == 'VP8':
        return vp8_depayload(payload)
    elif codec.name == 'H264':
        return h264_depayload(payload)
    else:
        return payload


def get_capabilities(kind):
    if kind in CODECS:
        codecs = []
        rtx_added = False
        for params in CODECS[kind]:
            if not is_rtx(params):
                codecs.append(RTCRtpCodecCapability(
                    mimeType=params.mimeType,
                    clockRate=params.clockRate,
                    channels=params.channels,
                    parameters=params.parameters))
            elif not rtx_added:
                # There will only be a single entry in codecs[] for retransmission
                # via RTX, with sdpFmtpLine not present.
                codecs.append(RTCRtpCodecCapability(
                    mimeType=params.mimeType,
                    clockRate=params.clockRate))
                rtx_added = True

        headerExtensions = []
        for params in HEADER_EXTENSIONS[kind]:
            headerExtensions.append(RTCRtpHeaderExtensionCapability(
                uri=params.uri))
        return RTCRtpCapabilities(codecs=codecs, headerExtensions=headerExtensions)


def get_decoder(codec):
    mimeType = codec.mimeType.lower()

    if mimeType == 'audio/opus':
        return OpusDecoder()
    elif mimeType == 'audio/pcma':
        return PcmaDecoder()
    elif mimeType == 'audio/pcmu':
        return PcmuDecoder()
    elif mimeType == 'video/h264':
        return H264Decoder()
    elif mimeType == 'video/vp8':
        return Vp8Decoder()


def get_encoder(codec):
    mimeType = codec.mimeType.lower()

    if mimeType == 'audio/opus':
        return OpusEncoder()
    elif mimeType == 'audio/pcma':
        return PcmaEncoder()
    elif mimeType == 'audio/pcmu':
        return PcmuEncoder()
    elif mimeType == 'video/h264':
        return H264Encoder()
    elif mimeType == 'video/vp8':
        return Vp8Encoder()


def is_rtx(codec):
    return codec.name.lower() == 'rtx'


init_codecs()
