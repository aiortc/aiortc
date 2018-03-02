import audioop

from ..mediastreams import AudioFrame

SAMPLE_RATE = 8000


def mono_8khz(frame):
    data = frame.data

    # resample at 8 kHz
    if frame.sample_rate != SAMPLE_RATE:
        data, _ = audioop.ratecv(
            data,
            frame.sample_width,
            frame.channels,
            frame.sample_rate,
            SAMPLE_RATE,
            None)

    # convert to mono
    if frame.channels == 2:
        data = audioop.tomono(data, frame.sample_width, 1, 1)

    return data


class PcmaDecoder:
    def decode(self, data):
        return AudioFrame(
            channels=1,
            data=audioop.alaw2lin(data, 2),
            sample_rate=8000)


class PcmaEncoder:
    timestamp_increment = 160

    def encode(self, frame):
        return audioop.lin2alaw(mono_8khz(frame), frame.sample_width)


class PcmuDecoder:
    def decode(self, data):
        return AudioFrame(
            channels=1,
            data=audioop.ulaw2lin(data, 2),
            sample_rate=8000)


class PcmuEncoder:
    timestamp_increment = 160

    def encode(self, frame):
        return audioop.lin2ulaw(mono_8khz(frame), frame.sample_width)
