import fractions

from aiortc.codecs import PCMA_CODEC, PCMU_CODEC, get_decoder, get_encoder
from aiortc.codecs.g711 import (PcmaDecoder, PcmaEncoder, PcmuDecoder,
                                PcmuEncoder)
from aiortc.jitterbuffer import JitterFrame

from .codecs import CodecTestCase


class PcmaTest(CodecTestCase):
    def test_decoder(self):
        decoder = get_decoder(PCMA_CODEC)
        self.assertTrue(isinstance(decoder, PcmaDecoder))

        frames = decoder.decode(JitterFrame(data=b'\xd5' * 160, timestamp=0))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.format.name, 's16')
        self.assertEqual(frame.layout.name, 'mono')
        self.assertEqual(bytes(frame.planes[0]), b'\x08\x00' * 160)
        self.assertEqual(frame.pts, 0)
        self.assertEqual(frame.samples, 160)
        self.assertEqual(frame.sample_rate, 8000)
        self.assertEqual(frame.time_base, fractions.Fraction(1, 8000))

    def test_encoder_mono_8hz(self):
        encoder = get_encoder(PCMA_CODEC)
        self.assertTrue(isinstance(encoder, PcmaEncoder))

        for frame in self.create_audio_frames(layout='mono', sample_rate=8000, count=10):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(payloads, [b'\xd5' * 160])
            self.assertEqual(timestamp, frame.pts)

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(PCMA_CODEC)
        self.assertTrue(isinstance(encoder, PcmaEncoder))

        for frame in self.create_audio_frames(layout='stereo', sample_rate=8000, count=10):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(payloads, [b'\xd5' * 160])
            self.assertEqual(timestamp, frame.pts)

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(PCMA_CODEC)
        self.assertTrue(isinstance(encoder, PcmaEncoder))

        for frame in self.create_audio_frames(layout='stereo', sample_rate=48000, count=10):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(payloads, [b'\xd5' * 160])
            self.assertEqual(timestamp, frame.pts // 6)

    def test_roundtrip(self):
        self.roundtrip_audio(PCMA_CODEC, output_layout='mono', output_sample_rate=8000)

    def test_roundtrip_with_loss(self):
        self.roundtrip_audio(PCMA_CODEC, output_layout='mono', output_sample_rate=8000, drop=[1])


class PcmuTest(CodecTestCase):
    def test_decoder(self):
        decoder = get_decoder(PCMU_CODEC)
        self.assertTrue(isinstance(decoder, PcmuDecoder))

        frames = decoder.decode(JitterFrame(data=b'\xff' * 160, timestamp=0))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.format.name, 's16')
        self.assertEqual(frame.layout.name, 'mono')
        self.assertEqual(bytes(frame.planes[0]), b'\x00\x00' * 160)
        self.assertEqual(frame.pts, 0)
        self.assertEqual(frame.samples, 160)
        self.assertEqual(frame.sample_rate, 8000)
        self.assertEqual(frame.time_base, fractions.Fraction(1, 8000))

    def test_encoder_mono_8hz(self):
        encoder = get_encoder(PCMU_CODEC)
        self.assertTrue(isinstance(encoder, PcmuEncoder))

        for frame in self.create_audio_frames(layout='mono', sample_rate=8000, count=10):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(payloads, [b'\xff' * 160])
            self.assertEqual(timestamp, frame.pts)

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(PCMU_CODEC)
        self.assertTrue(isinstance(encoder, PcmuEncoder))

        for frame in self.create_audio_frames(layout='stereo', sample_rate=8000, count=10):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(payloads, [b'\xff' * 160])
            self.assertEqual(timestamp, frame.pts)

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(PCMU_CODEC)
        self.assertTrue(isinstance(encoder, PcmuEncoder))

        for frame in self.create_audio_frames(layout='stereo', sample_rate=48000, count=10):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(payloads, [b'\xff' * 160])
            self.assertEqual(timestamp, frame.pts // 6)

    def test_roundtrip(self):
        self.roundtrip_audio(PCMU_CODEC, output_layout='mono', output_sample_rate=8000)

    def test_roundtrip_with_loss(self):
        self.roundtrip_audio(PCMU_CODEC, output_layout='mono', output_sample_rate=8000, drop=[1])
