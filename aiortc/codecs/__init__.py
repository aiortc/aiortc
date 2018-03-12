from ..rtcrtpparameters import RTCRtpCodecParameters
from .g711 import PcmaDecoder, PcmaEncoder, PcmuDecoder, PcmuEncoder
from .opus import OpusDecoder, OpusEncoder
from .vpx import VpxDecoder, VpxEncoder

PCMU_CODEC = RTCRtpCodecParameters(name='PCMU', clockRate=8000, channels=1, payloadType=0)
PCMA_CODEC = RTCRtpCodecParameters(name='PCMA', clockRate=8000, channels=1, payloadType=8)

MEDIA_CODECS = {
    'audio': [
        RTCRtpCodecParameters(name='opus', clockRate=48000, channels=2),
        PCMU_CODEC,
        PCMA_CODEC,
    ],
    'video': [
        RTCRtpCodecParameters(name='VP8', clockRate=90000),
    ]
}


def get_decoder(codec):
    if codec.name == 'opus':
        return OpusDecoder()
    elif codec.name == 'PCMU':
        return PcmuDecoder()
    elif codec.name == 'PCMA':
        return PcmaDecoder()
    elif codec.name == 'VP8':
        return VpxDecoder()


def get_encoder(codec):
    if codec.name == 'opus':
        return OpusEncoder()
    elif codec.name == 'PCMU':
        return PcmuEncoder()
    elif codec.name == 'PCMA':
        return PcmaEncoder()
    elif codec.name == 'VP8':
        return VpxEncoder()
