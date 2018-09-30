import fractions

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.opus import OpusDecoder, OpusEncoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .codecs import CodecTestCase

OPUS_CODEC = RTCRtpCodecParameters(name='opus', clockRate=48000, channels=2)


class OpusTest(CodecTestCase):
    def test_decoder(self):
        decoder = get_decoder(OPUS_CODEC)
        self.assertTrue(isinstance(decoder, OpusDecoder))

        frames = decoder.decode(JitterFrame(data=b'\xfc\xff\xfe', timestamp=0))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.channels, 2)
        self.assertEqual(frame.data, b'\x00' * 4 * 960)
        self.assertEqual(frame.sample_rate, 48000)
        self.assertEqual(frame.pts, 0)
        self.assertEqual(frame.time_base, fractions.Fraction(1, 48000))

    def test_encoder_mono_8khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        frames = self.create_audio_frames(channels=1, sample_rate=8000, count=2)

        # first frame
        payloads, timestamp = encoder.encode(frames[0])
        self.assertEqual(payloads, [b'\xfc\xff\xfe'])
        self.assertEqual(timestamp, 0)

        # second frame
        payloads, timestamp = encoder.encode(frames[1])
        self.assertEqual(timestamp, 960)

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        frames = self.create_audio_frames(channels=2, sample_rate=8000, count=2)

        # first frame
        payloads, timestamp = encoder.encode(frames[0])
        self.assertEqual(payloads, [b'\xfc\xff\xfe'])
        self.assertEqual(timestamp, 0)

        # second frame
        payloads, timestamp = encoder.encode(frames[1])
        self.assertEqual(timestamp, 960)

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        frames = self.create_audio_frames(channels=2, sample_rate=48000, count=2)

        # first frame
        payloads, timestamp = encoder.encode(frames[0])
        self.assertEqual(payloads, [b'\xfc\xff\xfe'])
        self.assertEqual(timestamp, 0)

        # second frame
        payloads, timestamp = encoder.encode(frames[1])
        self.assertEqual(timestamp, 960)

    def test_roundtrip(self):
        self.roundtrip_audio(OPUS_CODEC, output_channels=2, output_sample_rate=48000)
