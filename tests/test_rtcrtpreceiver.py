import asyncio
import contextlib
import fractions
from collections.abc import AsyncGenerator
from unittest import TestCase
from unittest.mock import MagicMock, patch

import av
from aiortc.codecs import PCMU_CODEC, get_encoder
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import MediaStreamError
from aiortc.rtcrtpparameters import (
    RTCRtpCapabilities,
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpDecodingParameters,
    RTCRtpHeaderExtensionCapability,
    RTCRtpReceiveParameters,
    RTCRtpRtxParameters,
)
from aiortc.rtcrtpreceiver import (
    NackGenerator,
    RemoteStreamTrack,
    RTCRtpReceiver,
    RTCRtpSynchronizationSource,
    StreamStatistics,
    TimestampMapper,
)
from aiortc.rtp import RtcpPacket, RtpPacket
from aiortc.stats import RTCStatsReport
from aiortc.utils import uint16_add

from .codecs import CodecTestCase
from .utils import ClosedDtlsTransport, asynctest, dummy_dtls_transport_pair, load

VP8_CODEC = RTCRtpCodecParameters(
    mimeType="video/VP8", clockRate=90000, payloadType=100
)


@contextlib.asynccontextmanager
async def create_receiver(kind: str) -> AsyncGenerator[RTCRtpReceiver, None]:
    async with dummy_dtls_transport_pair() as (local_transport, _):
        receiver = RTCRtpReceiver(kind, local_transport)
        assert receiver.transport == local_transport

        try:
            yield receiver
        finally:
            await receiver.stop()


def create_rtp_packets(count: int, seq: int = 0) -> list[RtpPacket]:
    packets = []
    for i in range(count):
        packets.append(
            RtpPacket(
                payload_type=0,
                sequence_number=uint16_add(seq, i),
                ssrc=1234,
                timestamp=i * 160,
            )
        )
    return packets


def create_rtp_video_packets(
    self: CodecTestCase, codec: RTCRtpCodecParameters, frames: int, seq: int = 0
) -> list[RtpPacket]:
    encoder = get_encoder(codec)
    packets = []
    for frame in self.create_video_frames(width=640, height=480, count=frames):
        payloads, timestamp = encoder.encode(frame)
        self.assertEqual(len(payloads), 1)
        packet = RtpPacket(
            payload_type=codec.payloadType,
            sequence_number=seq,
            ssrc=1234,
            timestamp=timestamp,
        )
        packet.payload = payloads[0]
        packet.marker = 1
        packets.append(packet)

        seq = uint16_add(seq, 1)
    return packets


class NackGeneratorTest(TestCase):
    def test_no_loss(self) -> None:
        generator = NackGenerator()

        for packet in create_rtp_packets(20, 0):
            missed = generator.add(packet)
            self.assertEqual(missed, False)

        self.assertEqual(generator.missing, set())

    def test_with_loss(self) -> None:
        generator = NackGenerator()

        # receive packets: 0, <1 missing>, 2
        packets = create_rtp_packets(3, 0)
        missing = packets.pop(1)
        for packet in packets:
            missed = generator.add(packet)
            self.assertEqual(missed, packet.sequence_number == 2)

        self.assertEqual(generator.missing, set([1]))

        # late arrival
        missed = generator.add(missing)
        self.assertEqual(missed, False)
        self.assertEqual(generator.missing, set())

    def test_with_loss_truncate(self) -> None:
        generator = NackGenerator()
        packets = create_rtp_packets(259, 0)

        generator.add(packets[0])
        generator.add(packets[129])
        self.assertEqual(generator.missing, set(range(1, 129)))

        generator.add(packets[258])
        self.assertEqual(generator.missing, set(range(130, 258)))


class StreamStatisticsTest(TestCase):
    def create_counter(self) -> StreamStatistics:
        return StreamStatistics(clockrate=8000)

    def test_no_loss(self) -> None:
        counter = self.create_counter()
        packets = create_rtp_packets(20, 0)

        # receive 10 packets
        for packet in packets[0:10]:
            counter.add(packet)

        self.assertEqual(counter.max_seq, 9)
        self.assertEqual(counter.packets_received, 10)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

        # receive 10 more packets
        for packet in packets[10:20]:
            counter.add(packet)

        self.assertEqual(counter.max_seq, 19)
        self.assertEqual(counter.packets_received, 20)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

    def test_no_loss_cycle(self) -> None:
        counter = self.create_counter()

        # receive 10 packets (with sequence cycle)
        for packet in create_rtp_packets(10, 65530):
            counter.add(packet)

        self.assertEqual(counter.max_seq, 3)
        self.assertEqual(counter.packets_received, 10)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

    def test_with_loss(self) -> None:
        counter = self.create_counter()
        packets = create_rtp_packets(20, 0)
        packets.pop(1)

        # receive 9 packets (one missing)
        for packet in packets[0:9]:
            counter.add(packet)

        self.assertEqual(counter.max_seq, 9)
        self.assertEqual(counter.packets_received, 9)
        self.assertEqual(counter.packets_lost, 1)
        self.assertEqual(counter.fraction_lost, 25)

        # receive 10 more packets
        for packet in packets[9:19]:
            counter.add(packet)

        self.assertEqual(counter.max_seq, 19)
        self.assertEqual(counter.packets_received, 19)
        self.assertEqual(counter.packets_lost, 1)
        self.assertEqual(counter.fraction_lost, 0)

    @patch("time.time")
    def test_no_jitter(self, mock_time: MagicMock) -> None:
        counter = self.create_counter()
        packets = create_rtp_packets(3, 0)

        mock_time.return_value = 1531562330.00
        counter.add(packets[0])
        self.assertEqual(counter._jitter_q4, 0)
        self.assertEqual(counter.jitter, 0)

        mock_time.return_value = 1531562330.02
        counter.add(packets[1])
        self.assertEqual(counter._jitter_q4, 0)
        self.assertEqual(counter.jitter, 0)

        mock_time.return_value = 1531562330.04
        counter.add(packets[2])
        self.assertEqual(counter._jitter_q4, 0)
        self.assertEqual(counter.jitter, 0)

    @patch("time.time")
    def test_with_jitter(self, mock_time: MagicMock) -> None:
        counter = self.create_counter()
        packets = create_rtp_packets(3, 0)

        mock_time.return_value = 1531562330.00
        counter.add(packets[0])
        self.assertEqual(counter._jitter_q4, 0)
        self.assertEqual(counter.jitter, 0)

        mock_time.return_value = 1531562330.03
        counter.add(packets[1])
        self.assertEqual(counter._jitter_q4, 80)
        self.assertEqual(counter.jitter, 5)

        mock_time.return_value = 1531562330.05
        counter.add(packets[2])
        self.assertEqual(counter._jitter_q4, 75)
        self.assertEqual(counter.jitter, 4)


class RTCRtpReceiverTest(CodecTestCase):
    def test_capabilities(self) -> None:
        # audio
        capabilities = RTCRtpReceiver.getCapabilities("audio")
        self.assertIsInstance(capabilities, RTCRtpCapabilities)
        self.assertEqual(
            capabilities.codecs,
            [
                RTCRtpCodecCapability(
                    mimeType="audio/opus", clockRate=48000, channels=2
                ),
                RTCRtpCodecCapability(
                    mimeType="audio/G722", clockRate=8000, channels=1
                ),
                RTCRtpCodecCapability(
                    mimeType="audio/PCMU", clockRate=8000, channels=1
                ),
                RTCRtpCodecCapability(
                    mimeType="audio/PCMA", clockRate=8000, channels=1
                ),
            ],
        )
        self.assertEqual(
            capabilities.headerExtensions,
            [
                RTCRtpHeaderExtensionCapability(
                    uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                ),
                RTCRtpHeaderExtensionCapability(
                    uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                ),
            ],
        )

        # video
        capabilities = RTCRtpReceiver.getCapabilities("video")
        self.assertIsInstance(capabilities, RTCRtpCapabilities)
        self.assertEqual(
            capabilities.codecs,
            [
                RTCRtpCodecCapability(mimeType="video/VP8", clockRate=90000),
                RTCRtpCodecCapability(mimeType="video/rtx", clockRate=90000),
                RTCRtpCodecCapability(
                    mimeType="video/H264",
                    clockRate=90000,
                    parameters={
                        "level-asymmetry-allowed": "1",
                        "packetization-mode": "1",
                        "profile-level-id": "42001f",
                    },
                ),
                RTCRtpCodecCapability(
                    mimeType="video/H264",
                    clockRate=90000,
                    parameters={
                        "level-asymmetry-allowed": "1",
                        "packetization-mode": "1",
                        "profile-level-id": "42e01f",
                    },
                ),
            ],
        )
        self.assertEqual(
            capabilities.headerExtensions,
            [
                RTCRtpHeaderExtensionCapability(
                    uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                ),
                RTCRtpHeaderExtensionCapability(
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time"
                ),
            ],
        )

        # bogus
        with self.assertRaises(ValueError):
            RTCRtpReceiver.getCapabilities("bogus")

    @asynctest
    async def test_connection_error(self) -> None:
        """
        Close the underlying transport before the receiver.
        """
        async with create_receiver("audio") as receiver:
            receiver._track = RemoteStreamTrack(kind="audio")
            receiver._set_rtcp_ssrc(1234)

            await receiver.receive(RTCRtpReceiveParameters(codecs=[PCMU_CODEC]))

            # receive a packet to prime RTCP
            packet = RtpPacket.parse(load("rtp.bin"))
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

            # break connection
            await receiver.transport.stop()

            # give RTCP time to send a report
            await asyncio.sleep(2)

    @patch("aiortc.rtcrtpreceiver.logger.isEnabledFor")
    @asynctest
    async def test_log_debug(self, mock_is_enabled_for: MagicMock) -> None:
        mock_is_enabled_for.return_value = True

        async with create_receiver("audio"):
            pass

    @asynctest
    async def test_rtp_and_rtcp(self) -> None:
        async with create_receiver("audio") as receiver:
            receiver._track = RemoteStreamTrack(kind="audio")
            self.assertEqual(receiver.track.readyState, "live")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[PCMU_CODEC]))

            # receive RTP
            for i in range(10):
                packet = RtpPacket.parse(load("rtp.bin"))
                packet.sequence_number += i
                packet.timestamp += i * 160
                await receiver._handle_rtp_packet(packet, arrival_time_ms=i * 20)

            # receive RTCP SR
            for rtcp_packet in RtcpPacket.parse(load("rtcp_sr.bin")):
                await receiver._handle_rtcp_packet(rtcp_packet)

            # check stats
            report = await receiver.getStats()
            self.assertIsInstance(report, RTCStatsReport)
            self.assertEqual(
                sorted([s.type for s in report.values()]),
                ["inbound-rtp", "remote-outbound-rtp", "transport"],
            )

            # check sources
            sources = receiver.getSynchronizationSources()
            self.assertEqual(len(sources), 1)
            self.assertIsInstance(sources[0], RTCRtpSynchronizationSource)
            self.assertEqual(sources[0].source, 4028317929)

            # check remote track
            frame = self.ensureIsInstance(await receiver.track.recv(), av.AudioFrame)
            self.assertEqual(frame.pts, 0)
            self.assertEqual(frame.sample_rate, 8000)
            self.assertEqual(frame.time_base, fractions.Fraction(1, 8000))

            frame = self.ensureIsInstance(await receiver.track.recv(), av.AudioFrame)
            self.assertEqual(frame.pts, 160)
            self.assertEqual(frame.sample_rate, 8000)
            self.assertEqual(frame.time_base, fractions.Fraction(1, 8000))

            # shutdown
            await receiver.stop()

            # read until end
            with self.assertRaises(MediaStreamError):
                while True:
                    await receiver.track.recv()
            self.assertEqual(receiver.track.readyState, "ended")

            # try reading again
            with self.assertRaises(MediaStreamError):
                await receiver.track.recv()

    @asynctest
    async def test_rtp_missing_video_packet(self) -> None:
        nacks = []
        pli = []

        async def mock_send_rtcp_nack(media_ssrc: int, lost: list[int]) -> None:
            nacks.append((media_ssrc, lost))

        async def mock_send_rtcp_pli(media_ssrc: int) -> None:
            pli.append(media_ssrc)

        async with create_receiver("video") as receiver:
            receiver._send_rtcp_nack = mock_send_rtcp_nack  # type: ignore
            receiver._send_rtcp_pli = mock_send_rtcp_pli  # type: ignore
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[VP8_CODEC]))

            # generate some packets
            packets = create_rtp_video_packets(self, codec=VP8_CODEC, frames=129)

            # receive RTP with a with a gap
            await receiver._handle_rtp_packet(packets[0], arrival_time_ms=0)
            await receiver._handle_rtp_packet(packets[128], arrival_time_ms=0)

            # check NACK was triggered
            lost_packets = list(range(1, 128))
            self.assertEqual(nacks[0], (1234, lost_packets))

            # check PLI was triggered
            self.assertEqual(pli, [1234])

    @asynctest
    async def test_rtp_empty_video_packet(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[VP8_CODEC]))

            # receive RTP with empty payload
            packet = RtpPacket(payload_type=100)
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

    @asynctest
    async def test_rtp_invalid_payload(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[VP8_CODEC]))

            # receive RTP with unknown payload type
            packet = RtpPacket(payload_type=100, payload=b"\x80")
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

    @asynctest
    async def test_rtp_unknown_payload_type(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[VP8_CODEC]))

            # receive RTP with unknown payload type
            packet = RtpPacket(payload_type=123)
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

    @asynctest
    async def test_rtp_disabled(self) -> None:
        async with create_receiver("audio") as receiver:
            self.assertEqual(receiver._enabled, True)
            receiver._track = RemoteStreamTrack(kind="audio")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[PCMU_CODEC]))
            receiver._enabled = False
            self.assertEqual(receiver._enabled, False)

            # receive RTP while disabled.
            packet = RtpPacket(payload_type=0)
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

            # check stats
            report = await receiver.getStats()
            self.assertIsInstance(report, RTCStatsReport)
            self.assertEqual(
                sorted([s.type for s in report.values()]),
                ["transport"],
            )

    @asynctest
    async def test_rtp_rtx(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(
                RTCRtpReceiveParameters(
                    codecs=[
                        VP8_CODEC,
                        RTCRtpCodecParameters(
                            mimeType="video/rtx",
                            clockRate=90000,
                            payloadType=101,
                            parameters={"apt": 100},
                        ),
                    ],
                    encodings=[
                        RTCRtpDecodingParameters(
                            ssrc=1234,
                            payloadType=100,
                            rtx=RTCRtpRtxParameters(ssrc=2345),
                        )
                    ],
                )
            )

            # receive RTX with payload
            packet = RtpPacket(payload_type=101, ssrc=2345, payload=b"\x00\x00")
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

            # receive RTX without payload
            packet = RtpPacket(payload_type=101, ssrc=2345)
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

    @asynctest
    async def test_rtp_rtx_unknown_ssrc(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(
                RTCRtpReceiveParameters(
                    codecs=[
                        VP8_CODEC,
                        RTCRtpCodecParameters(
                            mimeType="video/rtx",
                            clockRate=90000,
                            payloadType=101,
                            parameters={"apt": 100},
                        ),
                    ]
                )
            )

            # receive RTX with unknown SSRC
            packet = RtpPacket(payload_type=101, ssrc=1234)
            await receiver._handle_rtp_packet(packet, arrival_time_ms=0)

    @asynctest
    async def test_send_rtcp_nack(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._set_rtcp_ssrc(1234)
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[VP8_CODEC]))

            # send RTCP feedback NACK
            await receiver._send_rtcp_nack(5678, [7654])

    @asynctest
    async def test_send_rtcp_pli(self) -> None:
        async with create_receiver("video") as receiver:
            receiver._set_rtcp_ssrc(1234)
            receiver._track = RemoteStreamTrack(kind="video")

            await receiver.receive(RTCRtpReceiveParameters(codecs=[VP8_CODEC]))

            # send RTCP feedback PLI
            await receiver._send_rtcp_pli(5678)

    def test_invalid_dtls_transport_state(self) -> None:
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpReceiver("audio", dtlsTransport)  # type: ignore


class TimestampMapperTest(TestCase):
    def test_simple(self) -> None:
        mapper = TimestampMapper()
        self.assertEqual(mapper.map(1000), 0)
        self.assertEqual(mapper.map(1001), 1)
        self.assertEqual(mapper.map(1003), 3)
        self.assertEqual(mapper.map(1004), 4)
        self.assertEqual(mapper.map(1010), 10)

    def test_wrap(self) -> None:
        mapper = TimestampMapper()
        self.assertEqual(mapper.map(4294967293), 0)
        self.assertEqual(mapper.map(4294967294), 1)
        self.assertEqual(mapper.map(4294967295), 2)
        self.assertEqual(mapper.map(0), 3)
        self.assertEqual(mapper.map(1), 4)
