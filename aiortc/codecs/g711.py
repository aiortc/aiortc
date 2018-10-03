import audioop
import fractions

from ..mediastreams import AudioFrame

SAMPLE_RATE = 8000
TIME_BASE = fractions.Fraction(1, 8000)


class PcmDecoder:
    def decode(self, encoded_frame):
        frame = AudioFrame(
            channels=1,
            data=self._convert(encoded_frame.data, 2),
            sample_rate=SAMPLE_RATE)
        frame.pts = encoded_frame.timestamp
        frame.time_base = TIME_BASE
        return [frame]


class PcmEncoder:
    def __init__(self):
        self.rate_state = None

    def encode(self, frame, force_keyframe=False):
        data = frame.data
        timestamp = frame.pts

        # resample at 8 kHz
        if frame.sample_rate != SAMPLE_RATE:
            data, self.rate_state = audioop.ratecv(
                data,
                frame.sample_width,
                frame.channels,
                frame.sample_rate,
                SAMPLE_RATE,
                self.rate_state)
            timestamp = (timestamp * SAMPLE_RATE) // frame.sample_rate

        # convert to mono
        if frame.channels == 2:
            data = audioop.tomono(data, frame.sample_width, 1, 1)

        data = self._convert(data, frame.sample_width)
        return [data], timestamp


class PcmaEncoder(PcmEncoder):
    _convert = audioop.lin2alaw


class PcmaDecoder(PcmDecoder):
    _convert = audioop.alaw2lin


class PcmuDecoder(PcmDecoder):
    _convert = audioop.ulaw2lin


class PcmuEncoder(PcmEncoder):
    _convert = audioop.lin2ulaw
