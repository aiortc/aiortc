import asyncio
import logging
from unittest import TestCase

from aiowebrtc import RTCPeerConnection


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class RTCPeerConnectionTest(TestCase):
    def test_foo(self):
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        self.assertEqual(pc1.iceGatheringState, 'new')
        self.assertEqual(pc2.iceGatheringState, 'new')

        # create offer
        offer = run(pc1.createOffer())
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(offer['type'], 'offer')
        run(pc1.setLocalDescription(offer))

        # create answer
        run(pc2.setRemoteDescription(offer))
        answer = run(pc2.createAnswer())
        run(pc2.setLocalDescription(answer))

        # handle answer
        run(pc1.setRemoteDescription(answer))

        run(asyncio.sleep(1))


logging.basicConfig(level=logging.DEBUG)
