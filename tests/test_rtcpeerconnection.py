import asyncio
import logging
from unittest import TestCase

from aiowebrtc import (AudioStreamTrack, InvalidAccessError, InvalidStateError,
                       RTCPeerConnection, VideoStreamTrack)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def track_states(pc):
    states = {
        'iceConnectionState': [pc.iceConnectionState],
        'iceGatheringState': [pc.iceGatheringState],
        'signalingState': [pc.signalingState],
    }

    @pc.on('iceconnectionstatechange')
    def iceconnectionstatechange():
        states['iceConnectionState'].append(pc.iceConnectionState)

    @pc.on('icegatheringstatechange')
    def icegatheringstatechange():
        states['iceGatheringState'].append(pc.iceGatheringState)

    @pc.on('signalingstatechange')
    def signalingstatechange():
        states['signalingState'].append(pc.signalingState)

    return states


class RTCPeerConnectionTest(TestCase):
    def test_addTrack(self):
        pc = RTCPeerConnection()

        # add audio track
        track = AudioStreamTrack()
        sender = pc.addTrack(track)
        self.assertIsNotNone(sender)
        self.assertEqual(sender.track, track)
        self.assertEqual(pc.getSenders(), [sender])

        # try to add same track again
        with self.assertRaises(InvalidAccessError) as cm:
            pc.addTrack(track)
        self.assertEqual(str(cm.exception), 'Track already has a sender')

        # try adding another audio track
        with self.assertRaises(ValueError) as cm:
            pc.addTrack(AudioStreamTrack())
        self.assertEqual(str(cm.exception), 'Only a single audio track is supported for now')

        # try adding a video track
        with self.assertRaises(ValueError) as cm:
            pc.addTrack(VideoStreamTrack())
        self.assertEqual(str(cm.exception), 'Only a single audio track is supported for now')

    def test_addTrack_closed(self):
        pc = RTCPeerConnection()
        run(pc.close())
        with self.assertRaises(InvalidStateError) as cm:
            pc.addTrack(AudioStreamTrack())
        self.assertEqual(str(cm.exception), 'RTCPeerConnection is closed')

    def test_close(self):
        pc = RTCPeerConnection()
        pc_states = track_states(pc)

        # close once
        run(pc.close())

        # close twice
        run(pc.close())

        self.assertEqual(pc_states['signalingState'], ['stable', 'closed'])

    def test_connect(self):
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'new')
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, 'new')
        self.assertEqual(pc2.iceGatheringState, 'new')
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        offer = run(pc1.createOffer())
        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(offer.type, 'offer')

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)

        # FIXME!
        self.assertEqual(pc2.getReceivers(), [])

        # create answer
        answer = run(pc2.createAnswer())
        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(answer.type, 'answer')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # close
        run(pc1.close())
        run(pc2.close())
        self.assertEqual(pc1.iceConnectionState, 'closed')
        self.assertEqual(pc2.iceConnectionState, 'closed')

        # check state changes
        self.assertEqual(pc1_states['iceConnectionState'], [
            'new', 'checking', 'completed', 'closed'])
        self.assertEqual(pc1_states['iceGatheringState'], [
            'new', 'gathering', 'complete'])
        self.assertEqual(pc1_states['signalingState'], [
            'stable', 'have-local-offer', 'stable', 'closed'])

        self.assertEqual(pc2_states['iceConnectionState'], [
            'new', 'checking', 'completed', 'closed'])
        self.assertEqual(pc2_states['iceGatheringState'], [
            'new', 'gathering', 'complete'])
        self.assertEqual(pc2_states['signalingState'], [
            'stable', 'have-remote-offer', 'stable', 'closed'])


logging.basicConfig(level=logging.DEBUG)
