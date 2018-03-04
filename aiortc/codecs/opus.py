import audioop

from ..mediastreams import AudioFrame
from ._opus import ffi, lib

CHANNELS = 2
FRAME_SIZE = 960
SAMPLE_RATE = 48000
SAMPLE_WIDTH = 2


class OpusDecoder:
    def __init__(self):
        error = ffi.new('int *')
        self.decoder = lib.opus_decoder_create(SAMPLE_RATE, CHANNELS, error)
        assert error[0] == lib.OPUS_OK

        self.cdata = ffi.new('unsigned char []', FRAME_SIZE * CHANNELS * SAMPLE_WIDTH)
        self.buffer = ffi.buffer(self.cdata)

    def __del__(self):
        lib.opus_decoder_destroy(self.decoder)

    def decode(self, data):
        length = lib.opus_decode(self.decoder, data, len(data),
                                 ffi.cast('int16_t *', self.cdata), FRAME_SIZE, 0)
        assert length == FRAME_SIZE

        return AudioFrame(
            channels=CHANNELS,
            data=self.buffer[:],
            sample_rate=SAMPLE_RATE)


class OpusEncoder:
    timestamp_increment = FRAME_SIZE

    def __init__(self):
        error = ffi.new('int *')
        self.encoder = lib.opus_encoder_create(
            SAMPLE_RATE, CHANNELS, lib.OPUS_APPLICATION_VOIP, error)
        assert error[0] == lib.OPUS_OK

        self.cdata = ffi.new('unsigned char []', FRAME_SIZE * CHANNELS * SAMPLE_WIDTH)
        self.buffer = ffi.buffer(self.cdata)
        self.rate_state = None

    def __del__(self):
        lib.opus_encoder_destroy(self.encoder)

    def encode(self, frame):
        data = frame.data

        # resample at 48 kHz
        if frame.sample_rate != SAMPLE_RATE:
            data, self.rate_state = audioop.ratecv(
                data,
                frame.sample_width,
                frame.channels,
                frame.sample_rate,
                SAMPLE_RATE,
                self.rate_state)

        # convert to stereo
        if frame.channels == 1:
            data = audioop.tostereo(data, frame.sample_width, 1, 1)

        length = lib.opus_encode(self.encoder, ffi.cast('int16_t*', ffi.from_buffer(data)),
                                 FRAME_SIZE, self.cdata, len(self.cdata))
        assert length > 0
        return self.buffer[0:length]
