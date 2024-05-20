import fractions

from aiortc.codecs import get_decoder, get_encoder
from aiortc.codecs.opus import OpusDecoder, OpusEncoder
from aiortc.jitterbuffer import JitterFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .codecs import CodecTestCase

OPUS_CODEC = RTCRtpCodecParameters(
    mimeType="audio/opus", clockRate=48000, channels=2, payloadType=100
)
OPUS_PAYLOAD = b"\xfc\xff\xfe"


class OpusTest(CodecTestCase):
    def test_decoder(self):
        decoder = get_decoder(OPUS_CODEC)
        self.assertIsInstance(decoder, OpusDecoder)

        frames = decoder.decode(JitterFrame(data=OPUS_PAYLOAD, timestamp=0))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.format.name, "s16")
        self.assertEqual(frame.layout.name, "stereo")
        self.assertEqual(bytes(frame.planes[0]), b"\x00" * 4 * 960)
        self.assertEqual(frame.sample_rate, 48000)
        self.assertEqual(frame.pts, 0)
        self.assertEqual(frame.time_base, fractions.Fraction(1, 48000))

    def test_encoder_mono_8khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertIsInstance(encoder, OpusEncoder)

        output = [
            encoder.encode(frame)
            for frame in self.create_audio_frames(
                layout="mono", sample_rate=8000, count=3
            )
        ]
        self.assertEqual(
            output,
            [
                ([], None),  # No output due to buffering.
                ([OPUS_PAYLOAD], 0),
                ([OPUS_PAYLOAD], 960),
            ],
        )

    def test_encoder_stereo_8khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertIsInstance(encoder, OpusEncoder)

        output = [
            encoder.encode(frame)
            for frame in self.create_audio_frames(
                layout="stereo", sample_rate=8000, count=3
            )
        ]
        self.assertEqual(
            output,
            [
                ([], None),  # No output due to buffering.
                ([OPUS_PAYLOAD], 0),
                ([OPUS_PAYLOAD], 960),
            ],
        )

    def test_encoder_stereo_48khz(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertIsInstance(encoder, OpusEncoder)

        frames = self.create_audio_frames(layout="stereo", sample_rate=48000, count=2)

        # first frame
        payloads, timestamp = encoder.encode(frames[0])
        self.assertEqual(payloads, [OPUS_PAYLOAD])
        self.assertEqual(timestamp, 0)

        # second frame
        payloads, timestamp = encoder.encode(frames[1])
        self.assertEqual(timestamp, 960)

    def test_encoder_pack(self):
        encoder = get_encoder(OPUS_CODEC)
        self.assertTrue(isinstance(encoder, OpusEncoder))

        packet = self.create_packet(payload=OPUS_PAYLOAD, pts=1)
        payloads, timestamp = encoder.pack(packet)
        self.assertEqual(payloads, [OPUS_PAYLOAD])
        self.assertEqual(timestamp, 48)

    def test_roundtrip(self):
        self.roundtrip_audio(
            OPUS_CODEC,
            input_layout="stereo",
            input_sample_rate=48000,
            output_layout="stereo",
            output_sample_rate=48000,
        )

    def test_roundtrip_with_loss(self):
        self.roundtrip_audio(
            OPUS_CODEC,
            input_layout="stereo",
            input_sample_rate=48000,
            output_layout="stereo",
            output_sample_rate=48000,
            drop=[1],
        )
