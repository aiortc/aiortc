import audioop

from ..mediastreams import AudioFrame

SAMPLE_RATE = 8000


def mono_8khz(frame):
    data = frame.data
    timestamp = frame.timestamp

    # resample at 8 kHz
    if frame.sample_rate != SAMPLE_RATE:
        data, _ = audioop.ratecv(
            data,
            frame.sample_width,
            frame.channels,
            frame.sample_rate,
            SAMPLE_RATE,
            None)
        timestamp = (timestamp * SAMPLE_RATE) // frame.sample_rate

    # convert to mono
    if frame.channels == 2:
        data = audioop.tomono(data, frame.sample_width, 1, 1)

    return data, timestamp


class PcmaDecoder:
    def decode(self, encoded_frame):
        return [AudioFrame(
            channels=1,
            data=audioop.alaw2lin(encoded_frame.data, 2),
            sample_rate=8000,
            timestamp=encoded_frame.timestamp)]


class PcmaEncoder:
    def encode(self, frame, force_keyframe=False):
        data, timestamp = mono_8khz(frame)
        data = audioop.lin2alaw(data, frame.sample_width)
        return [data], timestamp


class PcmuDecoder:
    def decode(self, encoded_frame):
        return [AudioFrame(
            channels=1,
            data=audioop.ulaw2lin(encoded_frame.data, 2),
            sample_rate=8000,
            timestamp=encoded_frame.timestamp)]


class PcmuEncoder:
    def encode(self, frame, force_keyframe=False):
        data, timestamp = mono_8khz(frame)
        data = audioop.lin2ulaw(data, frame.sample_width)
        return [data], timestamp
