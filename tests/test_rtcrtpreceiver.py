import asyncio
from unittest import TestCase

from aiortc.codecs import PCMU_CODEC
from aiortc.exceptions import InvalidStateError
from aiortc.mediastreams import AudioFrame
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

        run(asyncio.gather(
            receiver._run(codec=PCMU_CODEC),
            transport.close()))

    def test_rtp_and_rtcp(self):
        transport, remote = dummy_dtls_transport_pair()

        receiver = RTCRtpReceiver('audio', transport)
        self.assertEqual(receiver.transport, transport)

        receiver._track = RemoteStreamTrack(kind='audio')

        task = asyncio.ensure_future(
            receiver._run(codec=PCMU_CODEC))

        # receive RTP
        run(remote.send(load('rtp.bin')))

        # receive RTCP
        run(remote.send(load('rtcp_sr.bin')))

        # receive garbage
        run(remote.send(b'garbage'))

        # check remote track
        frame = run(receiver._track.recv())
        self.assertTrue(isinstance(frame, AudioFrame))

        # shutdown
        run(transport.close())
        run(task)

    def test_invalid_dtls_transport_state(self):
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCRtpReceiver('audio', dtlsTransport)
