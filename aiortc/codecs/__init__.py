from .g711 import PcmaEncoder, PcmuEncoder
from .opus import OpusEncoder


def get_encoder(codec):
    if codec.name == 'opus':
        return OpusEncoder()
    elif codec.name == 'PCMU':
        return PcmuEncoder()
    elif codec.name == 'PCMA':
        return PcmaEncoder()
    else:
        return None
