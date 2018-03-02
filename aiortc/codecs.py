import audioop

import opuslib


SAMPLE_WIDTH = 2


class OpusEncoder:
    timestamp_increment = 960

    def __init__(self):
        self.encoder = opuslib.Encoder(
            fs=48000, channels=2, application='audio')
        self.rate_state = None

    def encode(self, frame):
        data = frame.data

        # resample at 48 kHz
        if frame.sample_rate != 48000:
            data, self.rate_state = audioop.ratecv(
                data,
                SAMPLE_WIDTH,
                frame.channels,
                frame.sample_rate,
                48000,
                self.rate_state)

        # convert to stereo
        if frame.channels == 1:
            data = audioop.tostereo(data, frame.sample_width, 1, 1)

        return self.encoder.encode(data, 960)


class PcmaEncoder:
    timestamp_increment = 160

    def encode(self, frame):
        data = frame.data

        # convert to mono
        if frame.channels == 2:
            data = audioop.tomono(data, frame.sample_width, 1, 1)

        return audioop.lin2alaw(data, frame.sample_width)


class PcmuEncoder:
    timestamp_increment = 160

    def encode(self, frame):
        data = frame.data

        # convert to mono
        if frame.channels == 2:
            data = audioop.tomono(data, frame.sample_width, 1, 1)

        return audioop.lin2ulaw(data, frame.sample_width)


def get_encoder(codec):
    if codec.name == 'opus':
        return OpusEncoder()
    elif codec.name == 'PCMU':
        return PcmuEncoder()
    elif codec.name == 'PCMA':
        return PcmaEncoder()
    else:
        return None
