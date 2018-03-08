import asyncio
from unittest import TestCase

from aiortc.codecs.g711 import PcmuDecoder
from aiortc.mediastreams import AudioFrame
from aiortc.rtcrtpreceiver import RemoteStreamTrack, RTCRtpReceiver

from .utils import dummy_dtls_transport_pair, load, run


class RTCRtpReceiverTest(TestCase):
    def test_connection_error(self):
        transport, _ = dummy_dtls_transport_pair()
        decoder = PcmuDecoder()

        receiver = RTCRtpReceiver(kind='audio')
        self.assertEqual(receiver.transport, None)

        run(asyncio.gather(
            receiver._run(transport=transport, decoder=decoder, payload_type=0),
            transport.close()))

    def test_rtp_and_rtcp(self):
        transport, remote = dummy_dtls_transport_pair()
        decoder = PcmuDecoder()

        receiver = RTCRtpReceiver(kind='audio')
        receiver._track = RemoteStreamTrack(kind='audio')

        task = asyncio.ensure_future(
            receiver._run(transport=transport, decoder=decoder, payload_type=0))

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
