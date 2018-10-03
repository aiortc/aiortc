import audioop
import fractions

from av import AudioFrame

SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2
SAMPLES_PER_FRAME = 160
TIME_BASE = fractions.Fraction(1, 8000)


class PcmDecoder:
    def decode(self, encoded_frame):
        frame = AudioFrame(format='s16', layout='mono', samples=SAMPLES_PER_FRAME)
        frame.planes[0].update(self._convert(encoded_frame.data, SAMPLE_WIDTH))
        frame.pts = encoded_frame.timestamp
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = TIME_BASE
        return [frame]


class PcmEncoder:
    def __init__(self):
        self.rate_state = None

    def encode(self, frame, force_keyframe=False):
        assert frame.format.name == 's16'
        assert frame.layout.name in ['mono', 'stereo']

        channels = len(frame.layout.channels)
        data = bytes(frame.planes[0])
        timestamp = frame.pts

        # resample at 8 kHz
        if frame.sample_rate != SAMPLE_RATE:
            data, self.rate_state = audioop.ratecv(
                data,
                SAMPLE_WIDTH,
                channels,
                frame.sample_rate,
                SAMPLE_RATE,
                self.rate_state)
            timestamp = (timestamp * SAMPLE_RATE) // frame.sample_rate

        # convert to mono
        if channels == 2:
            data = audioop.tomono(data, SAMPLE_WIDTH, 1, 1)

        data = self._convert(data, SAMPLE_WIDTH)
        return [data], timestamp


class PcmaEncoder(PcmEncoder):
    _convert = audioop.lin2alaw


class PcmaDecoder(PcmDecoder):
    _convert = audioop.alaw2lin


class PcmuDecoder(PcmDecoder):
    _convert = audioop.ulaw2lin


class PcmuEncoder(PcmEncoder):
    _convert = audioop.lin2ulaw
