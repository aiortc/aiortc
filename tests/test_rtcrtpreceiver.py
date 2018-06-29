from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioFrame
from aiortc.rtcrtpparameters import RTCRtpParameters
from aiortc.rtcrtpreceiver import (LossCounter, RemoteStreamTrack,
                                   RTCRtpReceiver)
from aiortc.rtp import RtcpPacket, RtpPacket

from .utils import dummy_dtls_transport_pair, load, run


class ClosedDtlsTransport:
    state = 'closed'


class LossCounterTest(TestCase):
    def test_no_loss(self):
        # receive 10 packets
        counter = LossCounter(0)
        for seq in range(1, 10):
            counter.add(seq)
        self.assertEqual(counter.max_seq, 9)
        self.assertEqual(counter.packets_received, 10)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

        # receive 10 more packets
        for seq in range(10, 20):
            counter.add(seq)
        self.assertEqual(counter.max_seq, 19)
        self.assertEqual(counter.packets_received, 20)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

    def test_no_loss_cycle(self):
        counter = LossCounter(65530)
        counter.add(65531)
        counter.add(65532)
        counter.add(65533)
        counter.add(65534)
        counter.add(65535)
        counter.add(0)
        counter.add(1)
        counter.add(2)
        counter.add(3)
        self.assertEqual(counter.max_seq, 3)
        self.assertEqual(counter.packets_received, 10)
        self.assertEqual(counter.packets_lost, 0)
        self.assertEqual(counter.fraction_lost, 0)

    def test_with_loss(self):
        # receive 9 packets (one missing)
        counter = LossCounter(0)
        for seq in range(2, 10):
            counter.add(seq)
        self.assertEqual(counter.max_seq, 9)
        self.assertEqual(counter.packets_received, 9)
        self.assertEqual(counter.packets_lost, 1)
        self.assertEqual(counter.fraction_lost, 25)

        # receive 10 more packets
        for seq in range(10, 20):
            counter.add(seq)
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
