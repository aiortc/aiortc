import asyncio
import fractions
from unittest import TestCase
from unittest.mock import patch

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import MediaStreamError
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpParameters
from aiortc.rtcrtpreceiver import (NackGenerator, RemoteStreamTrack,
                                   RTCRtpReceiver, StreamStatistics,
                                   TimestampMapper)
from aiortc.rtp import RtcpPacket, RtpPacket
from aiortc.stats import RTCStatsReport
from aiortc.utils import uint16_add

from .utils import dummy_dtls_transport_pair, load, run


def create_rtp_packets(count, seq=0):
    packets = []
    for i in range(count):
        packets.append(RtpPacket(
            payload_type=0,
            sequence_number=uint16_add(seq, i),
            ssrc=1234,
            timestamp=i * 160))
    return packets


class ClosedDtlsTransport:
    state = 'closed'


class NackGeneratorTest(TestCase):
    def create_generator(self):
        class FakeReceiver:
            def __init__(self):
                self.nack = []
                self.pli = []

            async def _send_rtcp_nack(self, media_ssrc, lost):
                self.nack.append((media_ssrc, lost))

            async def _send_rtcp_pli(self, media_ssrc, lost):
                self.pli.append(media_ssrc)

        receiver = FakeReceiver()
        return NackGenerator(receiver), receiver

    def test_no_loss(self):
        generator, receiver = self.create_generator()

        for packet in create_rtp_packets(20, 0):
            run(generator.add(packet))

        self.assertEqual(receiver.nack, [])
        self.assertEqual(receiver.pli, [])
        self.assertEqual(generator.missing, set())

    def test_with_loss(self):
        generator, receiver = self.create_generator()

        # receive packets: 0, <1 missing>, 2
        packets = create_rtp_packets(3, 0)
        missing = packets.pop(1)
        for packet in packets:
            run(generator.add(packet))

        self.assertEqual(receiver.nack, [(1234, [1])])
        self.assertEqual(receiver.pli, [])
        self.assertEqual(generator.missing, set([1]))
        receiver.nack.clear()

        # late arrival
        run(generator.add(missing))
        self.assertEqual(receiver.nack, [])
        self.assertEqual(receiver.pli, [])
        self.assertEqual(generator.missing, set())


class StreamStatisticsTest(TestCase):
    def create_counter(self):
        return StreamStatistics(clockrate=8000, ssrc=0)

    def test_no_loss(self):
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

    def test_no_loss_cycle(self):
        counter = self.create_counter()

        # receive 10 packets (with sequence cycle)
        for packet in create_rtp_packets(10, 65530):
            counter.add(packet)

        self.assertEqual(counter.max_seq, 3)
        self.assertEqual(counter.packets_received, 10)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

    def test_with_loss(self):
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

    @patch('time.time')
    def test_no_jitter(self, mock_time):
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

    @patch('time.time')
    def test_with_jitter(self, mock_time):
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


class RTCRtpReceiverTest(TestCase):
    def test_connection_error(self):
        """
        Close the underlying transport before the receiver.
        """
        transport, _ = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('audio', transport)
        self.assertEqual(receiver.transport, transport)

        receiver._track = RemoteStreamTrack(kind='audio')
        receiver._ssrc = 1234
        run(receiver.receive(RTCRtpParameters(codecs=[PCMU_CODEC])))

        # receive a packet to prime RTCP
        packet = RtpPacket.parse(load('rtp.bin'))
        run(receiver._handle_rtp_packet(packet, arrival_time_ms=0))

        # break connection
        run(transport.stop())

        # give RTCP time to send a report
        run(asyncio.sleep(2))

        # shutdown
        run(receiver.stop())

    def test_rtp_and_rtcp(self):
        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('audio', transport)
        self.assertEqual(receiver.transport, transport)

        receiver._track = RemoteStreamTrack(kind='audio')
        self.assertEqual(receiver._track.readyState, 'live')
        run(receiver.receive(RTCRtpParameters(codecs=[PCMU_CODEC])))

        # receive RTP
        for i in range(10):
            packet = RtpPacket.parse(load('rtp.bin'))
            packet.sequence_number += i
            packet.timestamp += i * 160
            run(receiver._handle_rtp_packet(packet, arrival_time_ms=i * 20))

        # receive RTCP SR
        for packet in RtcpPacket.parse(load('rtcp_sr.bin')):
            run(receiver._handle_rtcp_packet(packet))

        # check stats
        report = run(receiver.getStats())
        self.assertTrue(isinstance(report, RTCStatsReport))
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            ['inbound-rtp', 'remote-outbound-rtp', 'transport'])

        # check remote track
        frame = run(receiver._track.recv())
        self.assertEqual(frame.pts, 0)
        self.assertEqual(frame.sample_rate, 8000)
        self.assertEqual(frame.time_base, fractions.Fraction(1, 8000))

        frame = run(receiver._track.recv())
        self.assertEqual(frame.pts, 160)
        self.assertEqual(frame.sample_rate, 8000)
        self.assertEqual(frame.time_base, fractions.Fraction(1, 8000))

        # shutdown
        run(receiver.stop())

        # read until end
        with self.assertRaises(MediaStreamError):
            while True:
                run(receiver._track.recv())
        self.assertEqual(receiver._track.readyState, 'ended')

        # try reading again
        with self.assertRaises(MediaStreamError):
            run(receiver._track.recv())

    def test_rtp_empty_video_packet(self):
        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('video', transport)
        self.assertEqual(receiver.transport, transport)

        receiver._track = RemoteStreamTrack(kind='video')
        run(receiver.receive(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTP with empty payload
        packet = RtpPacket(payload_type=100)
        run(receiver._handle_rtp_packet(packet, arrival_time_ms=0))

        # shutdown
        run(receiver.stop())

    def test_send_rtcp_nack(self):
        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('video', transport)
        receiver._ssrc = 1234
        receiver._track = RemoteStreamTrack(kind='video')

        run(receiver.receive(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # send RTCP feedback NACK
        run(receiver._send_rtcp_nack(5678, [7654]))

        # shutdown
        run(receiver.stop())

    def test_send_rtcp_pli(self):
        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('video', transport)
        receiver._ssrc = 1234
        receiver._track = RemoteStreamTrack(kind='video')

        run(receiver.receive(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # send RTCP feedback PLI
        run(receiver._send_rtcp_pli(5678))

        # shutdown
        run(receiver.stop())

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpReceiver('audio', dtlsTransport)


class TimestampMapperTest(TestCase):
    def test_simple(self):
        mapper = TimestampMapper()
        self.assertEqual(mapper.map(1000), 0)
        self.assertEqual(mapper.map(1001), 1)
        self.assertEqual(mapper.map(1003), 3)
        self.assertEqual(mapper.map(1004), 4)
        self.assertEqual(mapper.map(1010), 10)

    def test_wrap(self):
        mapper = TimestampMapper()
        self.assertEqual(mapper.map(4294967293), 0)
        self.assertEqual(mapper.map(4294967294), 1)
        self.assertEqual(mapper.map(4294967295), 2)
        self.assertEqual(mapper.map(0), 3)
        self.assertEqual(mapper.map(1), 4)
