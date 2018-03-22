import asyncio
from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioFrame
from aiortc.rtcrtpparameters import RTCRtpParameters
from aiortc.rtcrtpreceiver import RemoteStreamTrack, RTCRtpReceiver

from .utils import dummy_dtls_transport_pair, load, run


class ClosedDtlsTransport:
    state = 'closed'


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
        run(remote.send(load('rtp.bin')))

        # receive RTCP
        run(remote.send(load('rtcp_sr.bin')))

        # receive truncated RTCP
        run(remote.send(b'\x81\xca\x00'))

        # receive garbage
        run(remote.send(b'garbage'))

        # check remote track
        frame = run(receiver._track.recv())
        self.assertTrue(isinstance(frame, AudioFrame))

        # shutdown
        run(asyncio.sleep(0.1))
        receiver.stop()
        run(asyncio.sleep(0))

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpReceiver('audio', dtlsTransport)
