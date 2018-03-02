from .g711 import PcmaDecoder, PcmaEncoder, PcmuDecoder, PcmuEncoder
from .opus import OpusDecoder, OpusEncoder
from .vpx import VpxDecoder, VpxEncoder


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
