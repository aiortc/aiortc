import asyncio
import re
from unittest import TestCase

from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.exceptions import (InternalError, InvalidAccessError,
                               InvalidStateError)
from aiortc.mediastreams import (AudioStreamTrack, MediaStreamTrack,
                                 VideoStreamTrack)
from aiortc.rtcpeerconnection import find_common_codecs
from aiortc.rtcrtpparameters import RTCRtcpFeedback, RTCRtpCodecParameters
from aiortc.sdp import SessionDescription
from aiortc.stats import RTCStatsReport

from .utils import run

LONG_DATA = b'\xff' * 2000
STRIP_CANDIDATES_RE = re.compile('^a=(candidate:.*|end-of-candidates)\r\n', re.M)


class BogusStreamTrack(MediaStreamTrack):
    kind = 'bogus'


def mids(pc):
    mids = [x.mid for x in pc.getTransceivers()]
    if pc.sctp:
        mids.append(pc.sctp.mid)
    return mids


def strip_candidates(description):
    return RTCSessionDescription(
        sdp=STRIP_CANDIDATES_RE.sub('', description.sdp),
        type=description.type)


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


class RTCRtpCodecParametersTest(TestCase):
    def test_common_static(self):
        local_codecs = [
            RTCRtpCodecParameters(name='opus', clockRate=48000, channels=2),
            RTCRtpCodecParameters(name='PCMU', clockRate=8000, channels=1, payloadType=0),
            RTCRtpCodecParameters(name='PCMA', clockRate=8000, channels=1, payloadType=8)
        ]
        remote_codecs = [
            RTCRtpCodecParameters(name='PCMA', clockRate=8000, payloadType=8),
            RTCRtpCodecParameters(name='PCMU', clockRate=8000, payloadType=0),
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(len(common), 2)
        self.assertEqual(common[0].clockRate, 8000)
        self.assertEqual(common[0].name, 'PCMA')
        self.assertEqual(common[0].payloadType, 8)
        self.assertEqual(common[1].clockRate, 8000)
        self.assertEqual(common[1].name, 'PCMU')
        self.assertEqual(common[1].payloadType, 0)

    def test_common_dynamic(self):
        local_codecs = [
            RTCRtpCodecParameters(name='opus', clockRate=48000, channels=2),
            RTCRtpCodecParameters(name='PCMU', clockRate=8000, channels=1, payloadType=0),
            RTCRtpCodecParameters(name='PCMA', clockRate=8000, channels=1, payloadType=8)
        ]
        remote_codecs = [
            RTCRtpCodecParameters(name='opus', clockRate=48000, payloadType=100),
            RTCRtpCodecParameters(name='PCMA', clockRate=8000, payloadType=8),
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(len(common), 2)
        self.assertEqual(common[0].clockRate, 48000)
        self.assertEqual(common[0].name, 'opus')
        self.assertEqual(common[0].payloadType, 100)
        self.assertEqual(common[1].clockRate, 8000)
        self.assertEqual(common[1].name, 'PCMA')
        self.assertEqual(common[1].payloadType, 8)

    def test_common_feedback(self):
        local_codecs = [
            RTCRtpCodecParameters(
                name='VP8',
                clockRate=90000,
                rtcpFeedback=[
                    RTCRtcpFeedback(type='nack'),
                    RTCRtcpFeedback(type='nack', parameter='pli'),
                ]
            )
        ]
        remote_codecs = [
            RTCRtpCodecParameters(
                name='VP8',
                clockRate=90000,
                payloadType=120,
                rtcpFeedback=[
                    RTCRtcpFeedback(type='nack'),
                    RTCRtcpFeedback(type='nack', parameter='sli'),
                ]
            )
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(len(common), 1)
        self.assertEqual(common[0].clockRate, 90000)
        self.assertEqual(common[0].name, 'VP8')
        self.assertEqual(common[0].payloadType, 120)
        self.assertEqual(common[0].rtcpFeedback, [
            RTCRtcpFeedback(type='nack'),
        ])


class RTCPeerConnectionTest(TestCase):
    def assertBundled(self, pc):
        transceivers = pc.getTransceivers()
        self.assertEqual(transceivers[0].receiver.transport, transceivers[0].sender.transport)
        transport = transceivers[0].receiver.transport
        for i in range(1, len(transceivers)):
            self.assertEqual(transceivers[i].receiver.transport, transport)
            self.assertEqual(transceivers[i].sender.transport, transport)
        if pc.sctp:
            self.assertEqual(pc.sctp.transport, transport)

    def test_addIceCandidate_no_sdpMid_or_sdpMLineIndex(self):
        pc = RTCPeerConnection()
        with self.assertRaises(ValueError) as cm:
            pc.addIceCandidate(RTCIceCandidate(
                component=1,
                foundation='0',
                ip='192.168.99.7',
                port=33543,
                priority=2122252543,
                protocol='UDP',
                type='host'))
        self.assertEqual(str(cm.exception), 'Candidate must have either sdpMid or sdpMLineIndex')

    def test_addTrack_audio(self):
        pc = RTCPeerConnection()

        # add audio track
        track1 = AudioStreamTrack()
        sender1 = pc.addTrack(track1)
        self.assertIsNotNone(sender1)
        self.assertEqual(sender1.track, track1)
        self.assertEqual(pc.getSenders(), [sender1])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # try to add same track again
        with self.assertRaises(InvalidAccessError) as cm:
            pc.addTrack(track1)
        self.assertEqual(str(cm.exception), 'Track already has a sender')

        # add another audio track
        track2 = AudioStreamTrack()
        sender2 = pc.addTrack(track2)
        self.assertIsNotNone(sender2)
        self.assertEqual(sender2.track, track2)
        self.assertEqual(pc.getSenders(), [sender1, sender2])
        self.assertEqual(len(pc.getTransceivers()), 2)

    def test_addTrack_bogus(self):
        pc = RTCPeerConnection()

        # try adding a bogus track
        with self.assertRaises(InternalError) as cm:
            pc.addTrack(BogusStreamTrack())
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    def test_addTrack_video(self):
        pc = RTCPeerConnection()

        # add video track
        video_track1 = VideoStreamTrack()
        video_sender1 = pc.addTrack(video_track1)
        self.assertIsNotNone(video_sender1)
        self.assertEqual(video_sender1.track, video_track1)
        self.assertEqual(pc.getSenders(), [video_sender1])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # try to add same track again
        with self.assertRaises(InvalidAccessError) as cm:
            pc.addTrack(video_track1)
        self.assertEqual(str(cm.exception), 'Track already has a sender')

        # add another video track
        video_track2 = VideoStreamTrack()
        video_sender2 = pc.addTrack(video_track2)
        self.assertIsNotNone(video_sender2)
        self.assertEqual(video_sender2.track, video_track2)
        self.assertEqual(pc.getSenders(), [video_sender1, video_sender2])
        self.assertEqual(len(pc.getTransceivers()), 2)

        # add audio track
        audio_track = AudioStreamTrack()
        audio_sender = pc.addTrack(audio_track)
        self.assertIsNotNone(audio_sender)
        self.assertEqual(audio_sender.track, audio_track)
        self.assertEqual(pc.getSenders(), [video_sender1, video_sender2, audio_sender])
        self.assertEqual(len(pc.getTransceivers()), 3)

    def test_addTrack_closed(self):
        pc = RTCPeerConnection()
        run(pc.close())
        with self.assertRaises(InvalidStateError) as cm:
            pc.addTrack(AudioStreamTrack())
        self.assertEqual(str(cm.exception), 'RTCPeerConnection is closed')

    def test_addTransceiver_audio_inactive(self):
        pc = RTCPeerConnection()

        # add transceiver
        transceiver = pc.addTransceiver('audio', direction='inactive')
        self.assertIsNotNone(transceiver)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, 'inactive')
        self.assertEqual(transceiver.sender.track, None)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver.sender])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # add track
        track = AudioStreamTrack()
        pc.addTrack(track)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, 'sendonly')
        self.assertEqual(transceiver.sender.track, track)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(len(pc.getTransceivers()), 1)

        # stop transceiver
        run(transceiver.stop())
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, 'sendonly')
        self.assertEqual(transceiver.sender.track, track)
        self.assertEqual(transceiver.stopped, True)

    def test_addTransceiver_audio_sendrecv(self):
        pc = RTCPeerConnection()

        # add transceiver
        transceiver = pc.addTransceiver('audio')
        self.assertIsNotNone(transceiver)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, 'sendrecv')
        self.assertEqual(transceiver.sender.track, None)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver.sender])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # add track
        track = AudioStreamTrack()
        pc.addTrack(track)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, 'sendrecv')
        self.assertEqual(transceiver.sender.track, track)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(len(pc.getTransceivers()), 1)

    def test_addTransceiver_audio_track(self):
        pc = RTCPeerConnection()

        # add audio track
        track1 = AudioStreamTrack()
        transceiver1 = pc.addTransceiver(track1)
        self.assertIsNotNone(transceiver1)
        self.assertEqual(transceiver1.currentDirection, None)
        self.assertEqual(transceiver1.direction, 'sendrecv')
        self.assertEqual(transceiver1.sender.track, track1)
        self.assertEqual(transceiver1.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver1.sender])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # try to add same track again
        with self.assertRaises(InvalidAccessError) as cm:
            pc.addTransceiver(track1)
        self.assertEqual(str(cm.exception), 'Track already has a sender')

        # add another audio track
        track2 = AudioStreamTrack()
        transceiver2 = pc.addTransceiver(track2)
        self.assertIsNotNone(transceiver2)
        self.assertEqual(transceiver2.currentDirection, None)
        self.assertEqual(transceiver2.direction, 'sendrecv')
        self.assertEqual(transceiver2.sender.track, track2)
        self.assertEqual(transceiver2.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver1.sender, transceiver2.sender])
        self.assertEqual(len(pc.getTransceivers()), 2)

    def test_addTransceiver_bogus_direction(self):
        pc = RTCPeerConnection()

        # try adding a bogus kind
        with self.assertRaises(InternalError) as cm:
            pc.addTransceiver('audio', direction='bogus')
        self.assertEqual(str(cm.exception), 'Invalid direction "bogus"')

    def test_addTransceiver_bogus_kind(self):
        pc = RTCPeerConnection()

        # try adding a bogus kind
        with self.assertRaises(InternalError) as cm:
            pc.addTransceiver('bogus')
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    def test_addTransceiver_bogus_track(self):
        pc = RTCPeerConnection()

        # try adding a bogus track
        with self.assertRaises(InternalError) as cm:
            pc.addTransceiver(BogusStreamTrack())
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    def test_close(self):
        pc = RTCPeerConnection()
        pc_states = track_states(pc)

        # close once
        run(pc.close())

        # close twice
        run(pc.close())

        self.assertEqual(pc_states['signalingState'], ['stable', 'closed'])

    def test_connect_audio_bidirectional(self):
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
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(mids(pc2), ['0'])
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertEqual(pc2.getTransceivers()[0].direction, 'sendrecv')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')
        self.assertEqual(pc1.getTransceivers()[0].direction, 'sendrecv')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # allow media to flow long enough to collect stats
        run(asyncio.sleep(2))

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

    def test_connect_audio_bidirectional_trickle(self):
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
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # strip out candidates
        desc1 = strip_candidates(pc1.localDescription)

        # handle offer
        run(pc2.setRemoteDescription(desc1))
        self.assertEqual(pc2.remoteDescription, desc1)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(mids(pc2), ['0'])
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

        # strip out candidates
        desc2 = strip_candidates(pc2.localDescription)

        # handle answer
        run(pc1.setRemoteDescription(desc2))
        self.assertEqual(pc1.remoteDescription, desc2)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # trickle candidates
        for transceiver in pc2.getTransceivers():
            iceGatherer = transceiver.sender.transport.transport.iceGatherer
            for candidate in iceGatherer.getLocalCandidates():
                candidate.sdpMid = transceiver.mid
                pc1.addIceCandidate(candidate)
        for transceiver in pc1.getTransceivers():
            iceGatherer = transceiver.sender.transport.transport.iceGatherer
            for candidate in iceGatherer.getLocalCandidates():
                candidate.sdpMid = transceiver.mid
                pc2.addIceCandidate(candidate)

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

    def test_connect_audio_mid_changes(self):
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

        # add audio tracks immediately
        pc1.addTrack(AudioStreamTrack())
        pc1.getTransceivers()[0].mid = 'sdparta_0'  # pretend we're Firefox!

        pc2.addTrack(AudioStreamTrack())

        # create offer
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['sdparta_0'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)
        self.assertTrue('a=mid:sdparta_0' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['sdparta_0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertTrue('a=mid:sdparta_0' in pc2.localDescription.sdp)

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

    def test_connect_audio_offer_recvonly_answer_recvonly(self):
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
        pc1.addTransceiver('audio', direction='recvonly')
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=recvonly' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(mids(pc2), ['0'])
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=inactive' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, 'inactive')
        self.assertEqual(pc2.getTransceivers()[0].direction, 'recvonly')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, 'inactive')
        self.assertEqual(pc1.getTransceivers()[0].direction, 'recvonly')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # allow media to flow long enough to collect stats
        run(asyncio.sleep(2))

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

    def test_connect_audio_offer_recvonly(self):
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
        pc1.addTransceiver('audio', direction='recvonly')
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=recvonly' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(mids(pc2), ['0'])
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendonly' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, 'sendonly')
        self.assertEqual(pc2.getTransceivers()[0].direction, 'sendrecv')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, 'recvonly')
        self.assertEqual(pc1.getTransceivers()[0].direction, 'recvonly')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # allow media to flow long enough to collect stats
        run(asyncio.sleep(2))

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

    def test_connect_audio_offer_sendonly(self):
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
        pc1.addTransceiver(AudioStreamTrack(), direction='sendonly')
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendonly' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(mids(pc2), ['0'])
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=recvonly' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, 'recvonly')
        self.assertEqual(pc2.getTransceivers()[0].direction, 'recvonly')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, 'sendonly')
        self.assertEqual(pc1.getTransceivers()[0].direction, 'sendonly')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # allow media to flow long enough to collect stats
        run(asyncio.sleep(2))

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

    def test_connect_audio_offer_sendrecv_answer_recvonly(self):
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
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=recvonly' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, 'recvonly')
        self.assertEqual(pc2.getTransceivers()[0].direction, 'recvonly')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, 'sendonly')
        self.assertEqual(pc1.getTransceivers()[0].direction, 'sendrecv')

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

    def test_connect_audio_offer_sendrecv_answer_sendonly(self):
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
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        pc2.getTransceivers()[0].direction = 'sendonly'
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendonly' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, 'sendonly')
        self.assertEqual(pc2.getTransceivers()[0].direction, 'sendonly')

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, 'recvonly')
        self.assertEqual(pc1.getTransceivers()[0].direction, 'sendrecv')

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

    def test_connect_audio_two_tracks(self):
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
        pc1.addTrack(AudioStreamTrack())
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0', '1'])
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ['0', '1'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertEqual(mids(pc2), ['0', '1'])
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

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

    def test_connect_audio_and_video(self):
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
        pc1.addTrack(VideoStreamTrack())
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertTrue('m=video ' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0', '1'])

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ['0', '1'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertTrue('m=video ' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('m=video ' in pc2.localDescription.sdp)

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

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

    def test_connect_audio_and_video_and_data_channel(self):
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
        pc1.addTrack(VideoStreamTrack())
        pc1.createDataChannel('chat', protocol='bob')
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertTrue('m=video ' in offer.sdp)
        self.assertTrue('m=application ' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0', '1', '2'])

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ['0', '1', '2'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertTrue('m=video ' in answer.sdp)
        self.assertTrue('m=application ' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('m=video ' in pc2.localDescription.sdp)
        self.assertTrue('m=application ' in pc2.localDescription.sdp)

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

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

    def test_connect_audio_and_video_and_data_channel_ice_fail(self):
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
        pc1.addTrack(VideoStreamTrack())
        pc1.createDataChannel('chat', protocol='bob')
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertTrue('m=video ' in offer.sdp)
        self.assertTrue('m=application ' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0', '1', '2'])

        # close one side
        pc1_description = pc1.localDescription
        run(pc1.close())

        # handle offer
        run(pc2.setRemoteDescription(pc1_description))
        self.assertEqual(pc2.remoteDescription, pc1_description)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ['0', '1', '2'])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertTrue('m=video ' in answer.sdp)
        self.assertTrue('m=application ' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('m=video ' in pc2.localDescription.sdp)
        self.assertTrue('m=application ' in pc2.localDescription.sdp)

        # check outcome
        done = asyncio.Event()

        @pc2.on('iceconnectionstatechange')
        def iceconnectionstatechange():
            done.set()

        run(done.wait())
        self.assertEqual(pc1.iceConnectionState, 'closed')
        self.assertEqual(pc2.iceConnectionState, 'failed')

        # close
        run(pc1.close())
        run(pc2.close())
        self.assertEqual(pc1.iceConnectionState, 'closed')
        self.assertEqual(pc2.iceConnectionState, 'closed')

        # check state changes
        self.assertEqual(pc1_states['iceConnectionState'], [
            'new', 'closed'])
        self.assertEqual(pc1_states['iceGatheringState'], [
            'new', 'gathering', 'complete'])
        self.assertEqual(pc1_states['signalingState'], [
            'stable', 'have-local-offer', 'closed'])

        self.assertEqual(pc2_states['iceConnectionState'], [
            'new', 'checking', 'failed', 'closed'])
        self.assertEqual(pc2_states['iceGatheringState'], [
            'new', 'gathering', 'complete'])
        self.assertEqual(pc2_states['signalingState'], [
            'stable', 'have-remote-offer', 'stable', 'closed'])

    def test_connect_video_bidirectional(self):
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
        pc1.addTrack(VideoStreamTrack())
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=video ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=video ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=video ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=video ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')

        # let media flow to trigger RTCP feedback, including REMB
        run(asyncio.sleep(5))

        # check stats
        report = run(pc1.getStats())
        self.assertTrue(isinstance(report, RTCStatsReport))
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            ['inbound-rtp', 'outbound-rtp', 'remote-inbound-rtp', 'remote-outbound-rtp',
             'transport'])

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

    def test_connect_video_h264(self):
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
        pc1.addTrack(VideoStreamTrack())
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=video ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=video ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # strip out vp8
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[0].rtp.codecs.pop(0)
        parsed.media[0].fmt.pop(0)
        desc1 = RTCSessionDescription(
            sdp=str(parsed),
            type=pc1.localDescription.type)
        self.assertFalse('VP8' in desc1.sdp)
        self.assertTrue('H264' in desc1.sdp)

        # handle offer
        run(pc2.setRemoteDescription(desc1))
        self.assertEqual(pc2.remoteDescription, desc1)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=video ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=video ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

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

    def test_connect_datachannel_legacy_sdp(self):
        pc1 = RTCPeerConnection()
        pc1._sctpLegacySdp = True
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on('datachannel')
        def on_datachannel(channel):
            self.assertEqual(channel.readyState, 'open')
            pc2_data_channels.append(channel)

            @channel.on('message')
            def on_message(message):
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send('string-echo: ' + message)
                else:
                    channel.send(b'binary-echo: ' + message)

        # create data channel
        dc = pc1.createDataChannel('chat', protocol='bob')
        self.assertEqual(dc.label, 'chat')
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, 'bob')
        self.assertEqual(dc.readyState, 'connecting')

        # send messages
        dc.send('hello')
        dc.send('')
        dc.send(b'\x00\x01\x02\x03')
        dc.send(b'')
        dc.send(LONG_DATA)
        with self.assertRaises(ValueError) as cm:
            dc.send(1234)
        self.assertEqual(str(cm.exception), "Cannot send unsupported data type: <class 'int'>")
        self.assertEqual(dc.bufferedAmount, 2011)

        @dc.on('message')
        def on_message(message):
            pc1_data_messages.append(message)

        # create offer
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=application ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=application ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sctpmap:5000 webrtc-datachannel 65535' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=application ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=application ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sctpmap:5000 webrtc-datachannel 65535' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')
        self.assertEqual(dc.readyState, 'open')
        self.assertEqual(dc.bufferedAmount, 0)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, 'chat')
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, 'bob')

        # check pc2 got messages
        run(asyncio.sleep(1))
        self.assertEqual(pc2_data_messages, [
            'hello',
            '',
            b'\x00\x01\x02\x03',
            b'',
            LONG_DATA,
        ])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, [
            'string-echo: hello',
            'string-echo: ',
            b'binary-echo: \x00\x01\x02\x03',
            b'binary-echo: ',
            b'binary-echo: ' + LONG_DATA,
        ])

        # close data channel
        dc.close()
        self.assertEqual(dc.readyState, 'closing')
        run(asyncio.sleep(0.5))
        self.assertEqual(dc.readyState, 'closed')

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

    def test_connect_datachannel_modern_sdp(self):
        pc1 = RTCPeerConnection()
        pc1._sctpLegacySdp = False
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on('datachannel')
        def on_datachannel(channel):
            self.assertEqual(channel.readyState, 'open')
            pc2_data_channels.append(channel)

            @channel.on('message')
            def on_message(message):
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send('string-echo: ' + message)
                else:
                    channel.send(b'binary-echo: ' + message)

        # create data channel
        dc = pc1.createDataChannel('chat', protocol='bob')
        self.assertEqual(dc.label, 'chat')
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, 'bob')
        self.assertEqual(dc.readyState, 'connecting')

        # send messages
        dc.send('hello')
        dc.send('')
        dc.send(b'\x00\x01\x02\x03')
        dc.send(b'')
        dc.send(LONG_DATA)
        with self.assertRaises(ValueError) as cm:
            dc.send(1234)
        self.assertEqual(str(cm.exception), "Cannot send unsupported data type: <class 'int'>")

        @dc.on('message')
        def on_message(message):
            pc1_data_messages.append(message)

        # create offer
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=application ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=application ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=sctp-port:5000' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=application ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=application ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=sctp-port:5000' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')
        self.assertEqual(dc.readyState, 'open')

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, 'chat')
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, 'bob')

        # check pc2 got messages
        run(asyncio.sleep(1))
        self.assertEqual(pc2_data_messages, [
            'hello',
            '',
            b'\x00\x01\x02\x03',
            b'',
            LONG_DATA,
        ])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, [
            'string-echo: hello',
            'string-echo: ',
            b'binary-echo: \x00\x01\x02\x03',
            b'binary-echo: ',
            b'binary-echo: ' + LONG_DATA,
        ])

        # close data channel
        dc.close()
        self.assertEqual(dc.readyState, 'closing')
        run(asyncio.sleep(0.5))
        self.assertEqual(dc.readyState, 'closed')

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

    def test_connect_datachannel_trickle(self):
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on('datachannel')
        def on_datachannel(channel):
            self.assertEqual(channel.readyState, 'open')
            pc2_data_channels.append(channel)

            @channel.on('message')
            def on_message(message):
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send('string-echo: ' + message)
                else:
                    channel.send(b'binary-echo: ' + message)

        # create data channel
        dc = pc1.createDataChannel('chat', protocol='bob')
        self.assertEqual(dc.label, 'chat')
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, 'bob')
        self.assertEqual(dc.readyState, 'connecting')

        # send messages
        dc.send('hello')
        dc.send('')
        dc.send(b'\x00\x01\x02\x03')
        dc.send(b'')
        dc.send(LONG_DATA)
        with self.assertRaises(ValueError) as cm:
            dc.send(1234)
        self.assertEqual(str(cm.exception), "Cannot send unsupported data type: <class 'int'>")

        @dc.on('message')
        def on_message(message):
            pc1_data_messages.append(message)

        # create offer
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=application ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=application ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # strip out candidates
        desc1 = strip_candidates(pc1.localDescription)

        # handle offer
        run(pc2.setRemoteDescription(desc1))
        self.assertEqual(pc2.remoteDescription, desc1)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=application ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=application ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

        # strip out candidates
        desc2 = strip_candidates(pc2.localDescription)

        # handle answer
        run(pc1.setRemoteDescription(desc2))
        self.assertEqual(pc1.remoteDescription, desc2)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # trickle candidates
        for candidate in pc2.sctp.transport.transport.iceGatherer.getLocalCandidates():
            candidate.sdpMid = pc2.sctp.mid
            pc1.addIceCandidate(candidate)
        for candidate in pc1.sctp.transport.transport.iceGatherer.getLocalCandidates():
            candidate.sdpMid = pc1.sctp.mid
            pc2.addIceCandidate(candidate)

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')
        self.assertEqual(dc.readyState, 'open')

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, 'chat')
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, 'bob')

        # check pc2 got messages
        run(asyncio.sleep(1))
        self.assertEqual(pc2_data_messages, [
            'hello',
            '',
            b'\x00\x01\x02\x03',
            b'',
            LONG_DATA,
        ])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, [
            'string-echo: hello',
            'string-echo: ',
            b'binary-echo: \x00\x01\x02\x03',
            b'binary-echo: ',
            b'binary-echo: ' + LONG_DATA,
        ])

        # close data channel
        dc.close()
        self.assertEqual(dc.readyState, 'closing')
        run(asyncio.sleep(0.5))
        self.assertEqual(dc.readyState, 'closed')

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

    def test_connect_datachannel_unordered(self):
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on('datachannel')
        def on_datachannel(channel):
            self.assertEqual(channel.readyState, 'open')
            pc2_data_channels.append(channel)

            @channel.on('message')
            def on_message(message):
                pc2_data_messages.append(message)
                channel.send('string-echo: ' + message)

        # create data channel
        dc = pc1.createDataChannel('chat', ordered=False, protocol='bob')
        self.assertEqual(dc.label, 'chat')
        self.assertEqual(dc.ordered, False)
        self.assertEqual(dc.protocol, 'bob')
        self.assertEqual(dc.readyState, 'connecting')

        # send message
        dc.send('hello')

        @dc.on('message')
        def on_message(message):
            pc1_data_messages.append(message)

        # create offer
        offer = run(pc1.createOffer())
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=application ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)
        self.assertFalse('a=end-of-candidates' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertEqual(mids(pc1), ['0'])
        self.assertTrue('m=application ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ['0'])

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=application ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)
        self.assertFalse('a=end-of-candidates' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=application ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=end-of-candidates' in pc2.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc2.localDescription.sdp)
        self.assertTrue('a=setup:active' in pc2.localDescription.sdp)

        # handle answer
        run(pc1.setRemoteDescription(pc2.localDescription))
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, 'checking')

        # check outcome
        run(asyncio.sleep(1))
        self.assertEqual(pc1.iceConnectionState, 'completed')
        self.assertEqual(pc2.iceConnectionState, 'completed')
        self.assertEqual(dc.readyState, 'open')

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, 'chat')
        self.assertEqual(pc2_data_channels[0].ordered, False)
        self.assertEqual(pc2_data_channels[0].protocol, 'bob')

        # check pc2 got message
        run(asyncio.sleep(1))
        self.assertEqual(pc2_data_messages, ['hello'])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, ['string-echo: hello'])

        # close data channel
        dc.close()
        self.assertEqual(dc.readyState, 'closing')
        run(asyncio.sleep(0.5))
        self.assertEqual(dc.readyState, 'closed')

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

    def test_createAnswer_closed(self):
        pc = RTCPeerConnection()
        run(pc.close())
        with self.assertRaises(InvalidStateError) as cm:
            run(pc.createAnswer())
        self.assertEqual(str(cm.exception), 'RTCPeerConnection is closed')

    def test_createAnswer_without_offer(self):
        pc = RTCPeerConnection()
        with self.assertRaises(InvalidStateError) as cm:
            run(pc.createAnswer())
        self.assertEqual(str(cm.exception), 'Cannot create answer in signaling state "stable"')

    def test_createOffer_closed(self):
        pc = RTCPeerConnection()
        run(pc.close())
        with self.assertRaises(InvalidStateError) as cm:
            run(pc.createOffer())
        self.assertEqual(str(cm.exception), 'RTCPeerConnection is closed')

    def test_createOffer_without_media(self):
        pc = RTCPeerConnection()
        with self.assertRaises(InternalError) as cm:
            run(pc.createOffer())
        self.assertEqual(str(cm.exception),
                         'Cannot create an offer with no media and no data channels')

    def test_setRemoteDescription_unexpected_answer(self):
        pc = RTCPeerConnection()
        with self.assertRaises(InvalidStateError) as cm:
            run(pc.setRemoteDescription(RTCSessionDescription(sdp='', type='answer')))
        self.assertEqual(str(cm.exception), 'Cannot handle answer in signaling state "stable"')

    def test_setRemoteDescription_unexpected_offer(self):
        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())
        offer = run(pc.createOffer())
        run(pc.setLocalDescription(offer))
        with self.assertRaises(InvalidStateError) as cm:
            run(pc.setRemoteDescription(RTCSessionDescription(sdp='', type='offer')))
        self.assertEqual(str(cm.exception),
                         'Cannot handle offer in signaling state "have-local-offer"')
