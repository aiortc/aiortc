import audioop
import fractions

from av import AudioFrame

from ._opus import ffi, lib

CHANNELS = 2
SAMPLE_RATE = 48000
SAMPLE_WIDTH = 2
SAMPLES_PER_FRAME = 960
TIME_BASE = fractions.Fraction(1, SAMPLE_RATE)


class OpusDecoder:
    def __init__(self):
        error = ffi.new('int *')
        self.decoder = lib.opus_decoder_create(SAMPLE_RATE, CHANNELS, error)
        assert error[0] == lib.OPUS_OK

    def __del__(self):
        lib.opus_decoder_destroy(self.decoder)

    def decode(self, encoded_frame):
        frame = AudioFrame(format='s16', layout='stereo', samples=SAMPLES_PER_FRAME)
        frame.pts = encoded_frame.timestamp
        frame.sample_rate = SAMPLE_RATE
        frame.time_base = TIME_BASE

        length = lib.opus_decode(self.decoder, encoded_frame.data, len(encoded_frame.data),
                                 ffi.cast('int16_t *', frame.planes[0].buffer_ptr),
                                 SAMPLES_PER_FRAME, 0)
        assert length == SAMPLES_PER_FRAME
        return [frame]


class OpusEncoder:
    def __init__(self):
        error = ffi.new('int *')
        self.encoder = lib.opus_encoder_create(
            SAMPLE_RATE, CHANNELS, lib.OPUS_APPLICATION_VOIP, error)
        assert error[0] == lib.OPUS_OK

        self.cdata = ffi.new('unsigned char []', SAMPLES_PER_FRAME * CHANNELS * SAMPLE_WIDTH)
        self.buffer = ffi.buffer(self.cdata)
        self.rate_state = None

    def __del__(self):
        lib.opus_encoder_destroy(self.encoder)

    def encode(self, frame, force_keyframe=False):
        assert frame.format.name == 's16'
        assert frame.layout.name in ['mono', 'stereo']

        channels = len(frame.layout.channels)
        data = bytes(frame.planes[0])
        timestamp = frame.pts

        # resample at 48 kHz
        if frame.sample_rate != SAMPLE_RATE:
            data, self.rate_state = audioop.ratecv(
                data,
                SAMPLE_WIDTH,
                channels,
                frame.sample_rate,
                SAMPLE_RATE,
                self.rate_state)
            timestamp = (timestamp * SAMPLE_RATE) // frame.sample_rate

        # convert to stereo
        if channels == 1:
            data = audioop.tostereo(data, SAMPLE_WIDTH, 1, 1)

        length = lib.opus_encode(self.encoder, ffi.cast('int16_t*', ffi.from_buffer(data)),
                                 SAMPLES_PER_FRAME, self.cdata, len(self.cdata))
        assert length > 0

        return [self.buffer[0:length]], timestamp
