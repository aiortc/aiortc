from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioFrame
from aiortc.rtcrtpparameters import RTCRtpParameters
from aiortc.rtcrtpreceiver import (RemoteStreamTrack, RTCRtpReceiver,
                                   StreamStatistics)
from aiortc.rtp import RTP_SEQ_MODULO, RtcpPacket, RtpPacket

from .utils import dummy_dtls_transport_pair, load, run


class ClosedDtlsTransport:
    state = 'closed'


class StreamStatisticsTest(TestCase):
    def create_packets(self, count, seq=0):
        packets = []
        for i in range(count):
            packets.append(RtpPacket(
                payload_type=0,
                sequence_number=(seq + i) % RTP_SEQ_MODULO,
                timestamp=i * 160))
        return packets

    def test_no_loss(self):
        counter = StreamStatistics(0)
        packets = self.create_packets(20, 0)

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
        counter = StreamStatistics(0)

        # receive 10 packets (with sequence cycle)
        for packet in self.create_packets(10, 65530):
            counter.add(packet)

        self.assertEqual(counter.max_seq, 3)
        self.assertEqual(counter.packets_received, 10)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

    def test_with_loss(self):
        counter = StreamStatistics(0)
        packets = self.create_packets(20, 0)
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


class RTCRtpReceiverTest(TestCase):
    def test_connection_error(self):
        """
        Close the underlying transport before the receiver.
        """
        transport, _ = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('audio', transport)
        self.assertEqual(receiver.transport, transport)

        run(receiver.receive(RTCRtpParameters(codecs=[PCMU_CODEC])))

        run(transport.close())

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

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpReceiver('audio', dtlsTransport)
