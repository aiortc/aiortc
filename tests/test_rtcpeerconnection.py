import asyncio
import logging
from unittest import TestCase

from aiowebrtc import RTCPeerConnection


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class RTCPeerConnectionTest(TestCase):
    def test_connect(self):
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'new')
        self.assertEqual(pc2.iceConnectionState, 'new')
        self.assertEqual(pc2.iceGatheringState, 'new')

        # create offer
        offer = run(pc1.createOffer())
        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(offer['type'], 'offer')

        # create answer
        run(pc2.setRemoteDescription(offer))
        answer = run(pc2.createAnswer())
        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(answer['type'], 'answer')

        # handle answer
        run(pc1.setRemoteDescription(answer))
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')


logging.basicConfig(level=logging.DEBUG)
