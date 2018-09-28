from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.opus import OpusDecoder, OpusEncoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.mediastreams import AudioFrame
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
        self.assertEqual(frame.timestamp, 0)

    def test_encoder_mono_8khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        frame = AudioFrame(
            channels=1,
            data=b'\x00\x00' * 160,
            sample_rate=8000,
            timestamp=0)
        payloads = encoder.encode(frame)
        self.assertEqual(payloads, [b'\xfc\xff\xfe'])

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 160,
            sample_rate=8000,
            timestamp=0)
        payloads = encoder.encode(frame)
        self.assertEqual(payloads, [b'\xfc\xff\xfe'])

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        frame = AudioFrame(
            channels=2,
            data=b'\x00\x00' * 2 * 960,
            sample_rate=48000,
            timestamp=0)
        payloads = encoder.encode(frame)
        self.assertEqual(payloads, [b'\xfc\xff\xfe'])

    def test_roundtrip(self):
        self.roundtrip_audio(OPUS_CODEC, output_channels=2, output_sample_rate=48000)
