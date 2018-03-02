import audioop

from ..mediastreams import AudioFrame
from ._opus import ffi, lib


class OpusDecoder:
    def __init__(self):
        error = ffi.new('int *')
        self.channels = 2
        self.frame_size = 960
        self.sample_width = 2
        self.decoder = lib.opus_decoder_create(48000, self.channels, error)
        assert error[0] == lib.OPUS_OK

        self.cdata = ffi.new('char []', self.frame_size * self.channels * self.sample_width)
        self.buffer = ffi.buffer(self.cdata)
        self.rate_state = None

    def __del__(self):
        lib.opus_decoder_destroy(self.decoder)

    def decode(self, data):
        length = lib.opus_decode(self.decoder, data, len(data),
                                 ffi.cast('int16_t *', self.cdata), self.frame_size, 0)
        assert length == self.frame_size

        # resample at 8 kHz
        data, self.rate_state = audioop.ratecv(
            self.buffer,
            self.sample_width,
            self.channels,
            48000,
            8000,
            self.rate_state)

        return AudioFrame(channels=2, data=data)


class OpusEncoder:
    timestamp_increment = 960

    def __init__(self):
        error = ffi.new('int *')
        self.encoder = lib.opus_encoder_create(48000, 2, lib.OPUS_APPLICATION_VOIP, error)
        assert error[0] == lib.OPUS_OK

        self.cdata = ffi.new('char []', 960)
        self.buffer = ffi.buffer(self.cdata)
        self.rate_state = None

    def __del__(self):
        lib.opus_encoder_destroy(self.encoder)

    def encode(self, frame):
        data = frame.data

        # resample at 48 kHz
        if frame.sample_rate != 48000:
            data, self.rate_state = audioop.ratecv(
                data,
                frame.sample_width,
                frame.channels,
                frame.sample_rate,
                48000,
                self.rate_state)

        # convert to stereo
        if frame.channels == 1:
            data = audioop.tostereo(data, frame.sample_width, 1, 1)

        length = lib.opus_encode(self.encoder, ffi.cast('int16_t*', ffi.from_buffer(data)),
                                 960, self.cdata, len(self.cdata))
        assert length > 0
        return self.buffer[0:length]
