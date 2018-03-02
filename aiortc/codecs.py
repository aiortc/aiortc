import audioop


class PcmaEncoder:
    def encode(self, frame):
        return audioop.lin2alaw(frame.data, 2)


class PcmuEncoder:
    def encode(self, frame):
        return audioop.lin2ulaw(frame.data, 2)


def get_encoder(codec):
    if codec.name == 'PCMU':
        return PcmuEncoder()
    elif codec.name == 'PCMA':
        return PcmaEncoder()
    else:
        return None
