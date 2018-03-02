import audioop

from ..mediastreams import AudioFrame


class PcmaDecoder:
    def decode(self, data):
        return AudioFrame(
            channels=1,
            data=audioop.alaw2lin(data, 2))


class PcmaEncoder:
    timestamp_increment = 160

    def encode(self, frame):
        data = frame.data

        # convert to mono
        if frame.channels == 2:
            data = audioop.tomono(data, frame.sample_width, 1, 1)

        return audioop.lin2alaw(data, frame.sample_width)


class PcmuDecoder:
    def decode(self, data):
        return AudioFrame(
            channels=1,
            data=audioop.ulaw2lin(data, 2))


class PcmuEncoder:
    timestamp_increment = 160

    def encode(self, frame):
        data = frame.data

        # convert to mono
        if frame.channels == 2:
            data = audioop.tomono(data, frame.sample_width, 1, 1)

        return audioop.lin2ulaw(data, frame.sample_width)
