import asyncio
from unittest import TestCase
from unittest.mock import patch

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioFrame
from aiortc.rtcrtpparameters import RTCRtpCodecParameters, RTCRtpParameters
from aiortc.rtcrtpreceiver import (NackGenerator, RemoteStreamTrack,
                                   RTCRtpReceiver, StreamStatistics)
from aiortc.rtp import (RTCP_PSFB_PLI, RTP_SEQ_MODULO, RtcpPacket,
                        RtcpPsfbPacket, RtcpRtpfbPacket, RtpPacket)

from .utils import dummy_dtls_transport_pair, load, run


def create_rtp_packets(count, seq=0):
    packets = []
    for i in range(count):
        packets.append(RtpPacket(
            payload_type=0,
            sequence_number=(seq + i) % RTP_SEQ_MODULO,
            ssrc=1234,
            timestamp=i * 160))
    return packets


class ClosedDtlsTransport:
    state = 'closed'


class NackGeneratorTest(TestCase):
    def create_generator(self):
        calls = []

        async def callback(ssrc, lost):
            calls.append((ssrc, lost))

        return NackGenerator(callback), calls

    def test_no_loss(self):
        generator, calls = self.create_generator()

        for packet in create_rtp_packets(20, 0):
            run(generator.add(packet))

        self.assertEqual(calls, [])
        self.assertEqual(generator.missing, set())

    def test_with_loss(self):
        generator, calls = self.create_generator()

        # receive packets: 0, <1 missing>, 2
        packets = create_rtp_packets(3, 0)
        missing = packets.pop(1)
        for packet in packets:
            run(generator.add(packet))

        self.assertEqual(calls, [
            (1234, [1]),
        ])
        self.assertEqual(generator.missing, set([1]))
        calls.clear()

        # late arrival
        run(generator.add(missing))
        self.assertEqual(calls, [])
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
        run(receiver._handle_rtp_packet(packet))

        run(transport.close())

        # give RTCP time to send a report
        run(asyncio.sleep(2))

    def test_rtp_and_rtcp(self):
        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('audio', transport)
        self.assertEqual(receiver.transport, transport)

        receiver._track = RemoteStreamTrack(kind='audio')
        run(receiver.receive(RTCRtpParameters(codecs=[PCMU_CODEC])))

        # receive RTP
        packet = RtpPacket.parse(load('rtp.bin'))
        run(receiver._handle_rtp_packet(packet))

        # receive RTCP
        for packet in RtcpPacket.parse(load('rtcp_sr.bin')):
            run(receiver._handle_rtcp_packet(packet))
        self.assertEqual(sorted(receiver._stats.keys()), [
            'remote-inbound-rtp',
            'remote-outbound-rtp'
        ])

        # check remote track
        frame = run(receiver._track.recv())
        self.assertTrue(isinstance(frame, AudioFrame))

        # shutdown
        run(receiver.stop())

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
        run(receiver._handle_rtp_packet(packet))

    def test_rtcp_nack(self):
        class DummySender:
            rtx = []

            async def _retransmit(self, sequence_number):
                self.rtx.append(sequence_number)

        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('video', transport)
        self.assertEqual(receiver.transport, transport)

        sender = DummySender()
        receiver._set_sender(sender)

        receiver._track = RemoteStreamTrack(kind='audio')
        run(receiver.receive(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP feedback NACK
        packet = RtcpRtpfbPacket(fmt=1, ssrc=1234, media_ssrc=5678)
        packet.lost.append(7654)
        run(receiver._handle_rtcp_packet(packet))

        self.assertEqual(sender.rtx, [7654])

    def test_rtcp_pli(self):
        class DummySender:
            kf = 0

            def _send_keyframe(self):
                self.kf += 1

        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('video', transport)
        self.assertEqual(receiver.transport, transport)

        sender = DummySender()
        receiver._set_sender(sender)

        receiver._track = RemoteStreamTrack(kind='audio')
        run(receiver.receive(RTCRtpParameters(codecs=[
            RTCRtpCodecParameters(name='VP8', clockRate=90000, payloadType=100),
        ])))

        # receive RTCP feedback NACK
        packet = RtcpPsfbPacket(fmt=RTCP_PSFB_PLI, ssrc=1234, media_ssrc=5678)
        run(receiver._handle_rtcp_packet(packet))

        self.assertEqual(sender.kf, 1)

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

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpReceiver('audio', dtlsTransport)
