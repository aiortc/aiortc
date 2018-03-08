import asyncio
from unittest import TestCase

from aiortc.codecs.g711 import PcmuDecoder
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioFrame
from aiortc.rtcrtpreceiver import RemoteStreamTrack, RTCRtpReceiver

from .utils import dummy_dtls_transport_pair, load, run


class ClosedDtlsTransport:
    state = 'closed'


class RTCRtpReceiverTest(TestCase):
    def test_connection_error(self):
        transport, _ = dummy_dtls_transport_pair()
        decoder = PcmuDecoder()

        receiver = RTCRtpReceiver('audio')
        self.assertEqual(receiver.transport, None)

        receiver.setTransport(transport)
        self.assertEqual(receiver.transport, transport)

        run(asyncio.gather(
            receiver._run(decoder=decoder, payload_type=0),
            transport.close()))

    def test_rtp_and_rtcp(self):
        transport, remote = dummy_dtls_transport_pair()
        decoder = PcmuDecoder()

        receiver = RTCRtpReceiver('audio')
        receiver._track = RemoteStreamTrack(kind='audio')
        receiver.setTransport(transport)

        task = asyncio.ensure_future(
            receiver._run(decoder=decoder, payload_type=0))

        # receive RTP
        run(remote.send(load('rtp.bin')))

        # receive RTCP
        run(remote.send(load('rtcp_sr.bin')))

        # check transport
        self.assertEqual(receiver.transport, transport)

        # check remote track
        frame = run(receiver._track.recv())
        self.assertTrue(isinstance(frame, AudioFrame))

        # shutdown
        run(transport.close())
        run(task)

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        receiver = RTCRtpReceiver('audio')
        with self.assertRaises(InvalidStateError):
            receiver.setTransport(dtlsTransport)
