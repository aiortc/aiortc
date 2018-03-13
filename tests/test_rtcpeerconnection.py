import asyncio
from unittest import TestCase

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.exceptions import (InternalError, InvalidAccessError,
                               InvalidStateError)
from aiortc.mediastreams import (AudioStreamTrack, MediaStreamTrack,
                                 VideoStreamTrack)
from aiortc.rtcpeerconnection import MEDIA_CODECS, find_common_codecs
from aiortc.rtcrtpparameters import RTCRtpCodecParameters

from .utils import run

LONG_DATA = b'\xff' * 2000


class BogusStreamTrack(MediaStreamTrack):
    kind = 'bogus'


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
        local_codecs = MEDIA_CODECS['audio'][:]
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
        local_codecs = MEDIA_CODECS['audio'][:]
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


class RTCPeerConnectionTest(TestCase):
    def test_addTrack_audio(self):
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
        with self.assertRaises(InternalError) as cm:
            pc.addTrack(AudioStreamTrack())
        self.assertEqual(str(cm.exception), 'Only a single audio track is supported for now')

    def test_addTrack_bogus(self):
        pc = RTCPeerConnection()

        # try adding a bogus track
        with self.assertRaises(InternalError) as cm:
            pc.addTrack(BogusStreamTrack)
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    def test_addTrack_video(self):
        pc = RTCPeerConnection()

        # add video track
        video_track = VideoStreamTrack()
        video_sender = pc.addTrack(video_track)
        self.assertIsNotNone(video_sender)
        self.assertEqual(video_sender.track, video_track)
        self.assertEqual(pc.getSenders(), [video_sender])

        # try to add same track again
        with self.assertRaises(InvalidAccessError) as cm:
            pc.addTrack(video_track)
        self.assertEqual(str(cm.exception), 'Track already has a sender')

        # try adding another video track
        with self.assertRaises(InternalError) as cm:
            pc.addTrack(VideoStreamTrack())
        self.assertEqual(str(cm.exception), 'Only a single video track is supported for now')

        # add audio track
        audio_track = AudioStreamTrack()
        audio_sender = pc.addTrack(audio_track)
        self.assertIsNotNone(audio_sender)
        self.assertEqual(audio_sender.track, audio_track)
        self.assertEqual(pc.getSenders(), [video_sender, audio_sender])

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
        self.assertEqual(offer.type, 'offer')
        self.assertTrue('m=audio ' in offer.sdp)
        self.assertFalse('a=candidate:' in offer.sdp)

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=recvonly' in pc2.localDescription.sdp)
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

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=audio ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=audio ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
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

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertTrue('m=video ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=video ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=video ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
        self.assertTrue('a=sendrecv' in pc1.localDescription.sdp)
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

    def test_connect_datachannel(self):
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

        run(pc1.setLocalDescription(offer))
        self.assertEqual(pc1.iceConnectionState, 'new')
        self.assertEqual(pc1.iceGatheringState, 'complete')
        self.assertTrue('m=application ' in pc1.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc1.localDescription.sdp)
        self.assertTrue('a=sctpmap:5000 webrtc-datachannel 65535' in pc1.localDescription.sdp)
        self.assertTrue('a=fingerprint:sha-256' in pc1.localDescription.sdp)
        self.assertTrue('a=setup:actpass' in pc1.localDescription.sdp)

        # handle offer
        run(pc2.setRemoteDescription(pc1.localDescription))
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)

        # create answer
        answer = run(pc2.createAnswer())
        self.assertEqual(answer.type, 'answer')
        self.assertTrue('m=application ' in answer.sdp)
        self.assertFalse('a=candidate:' in answer.sdp)

        run(pc2.setLocalDescription(answer))
        self.assertEqual(pc2.iceConnectionState, 'checking')
        self.assertEqual(pc2.iceGatheringState, 'complete')
        self.assertTrue('m=application ' in pc2.localDescription.sdp)
        self.assertTrue('a=candidate:' in pc2.localDescription.sdp)
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

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, 'chat')
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
