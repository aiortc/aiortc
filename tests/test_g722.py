from aiortc.codecs import G722_CODEC, get_decoder, get_encoder
from aiortc.codecs.g722 import G722Decoder, G722Encoder
from aiortc.jitterbuffer import JitterFrame

from .codecs import CodecTestCase

# silence
G722_PAYLOAD = b"\xfa" * 160


class G722Test(CodecTestCase):
    def test_decoder(self) -> None:
        decoder = get_decoder(G722_CODEC)
        self.assertIsInstance(decoder, G722Decoder)

        frames = decoder.decode(JitterFrame(data=G722_PAYLOAD, timestamp=0))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertAudioFrame(
            frame,
            data=None,
            layout="mono",
            pts=0,
            samples=320,
            sample_rate=16000,
        )

    def test_encoder_mono_16khz(self) -> None:
        encoder = get_encoder(G722_CODEC)
        self.assertIsInstance(encoder, G722Encoder)

        for frame in self.create_audio_frames(
            layout="mono", sample_rate=16000, count=10
        ):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(len(payloads), 1)
            self.assertEqual(len(payloads[0]), 160)
            self.assertEqual(timestamp, frame.pts // 2)

    def test_encoder_stereo_16khz(self) -> None:
        encoder = get_encoder(G722_CODEC)
        self.assertIsInstance(encoder, G722Encoder)

        for frame in self.create_audio_frames(
            layout="stereo", sample_rate=16000, count=10
        ):
            payloads, timestamp = encoder.encode(frame)
            self.assertEqual(len(payloads), 1)
            self.assertEqual(len(payloads[0]), 160)
            self.assertEqual(timestamp, frame.pts // 2)

    def test_encoder_stereo_48khz(self) -> None:
        encoder = get_encoder(G722_CODEC)
        self.assertIsInstance(encoder, G722Encoder)

        output = [
            encoder.encode(frame)
            for frame in self.create_audio_frames(
                layout="stereo", sample_rate=48000, count=10
            )
        ]
        self.assertEqual(
            [([len(p) for p in payloads], timestamp) for payloads, timestamp in output],
            [
                ([], None),  # No output due to buffering.
                ([160], 0),
                ([160], 160),
                ([160], 320),
                ([160], 480),
                ([160], 640),
                ([160], 800),
                ([160], 960),
                ([160], 1120),
                ([160], 1280),
            ],
        )

    def test_encoder_pack(self) -> None:
        encoder = get_encoder(G722_CODEC)
        self.assertTrue(isinstance(encoder, G722Encoder))

        packet = self.create_packet(payload=G722_PAYLOAD, pts=1)
        payloads, timestamp = encoder.pack(packet)
        self.assertEqual(payloads, [G722_PAYLOAD])
        self.assertEqual(timestamp, 8)

    def test_roundtrip(self) -> None:
        self.roundtrip_audio(G722_CODEC, layout="mono", sample_rate=16000)

    def test_roundtrip_with_loss(self) -> None:
        self.roundtrip_audio(G722_CODEC, layout="mono", sample_rate=16000, drop=[1])
