import asyncio
import re
from collections.abc import Callable
from typing import Optional, Union
from unittest import TestCase

import aioice.stun
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaPlayer
from aiortc.exceptions import (
    InternalError,
    InvalidAccessError,
    InvalidStateError,
    OperationError,
)
from aiortc.mediastreams import AudioStreamTrack, MediaStreamTrack, VideoStreamTrack
from aiortc.rtcpeerconnection import (
    filter_preferred_codecs,
    find_common_codecs,
    is_codec_compatible,
)
from aiortc.rtcrtpparameters import (
    RTCRtcpFeedback,
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
)
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.sdp import SessionDescription
from aiortc.stats import RTCStatsReport

from .test_contrib_media import MediaTestCase
from .utils import asynctest, lf2crlf

LONG_DATA = b"\xff" * 2000
STRIP_CANDIDATES_RE = re.compile("^a=(candidate:.*|end-of-candidates)\r\n", re.M)

H264_SDP = lf2crlf(
    """a=rtpmap:99 H264/90000
a=rtcp-fb:99 nack
a=rtcp-fb:99 nack pli
a=rtcp-fb:99 goog-remb
a=fmtp:99 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42001f
a=rtpmap:100 rtx/90000
a=fmtp:100 apt=99
a=rtpmap:101 H264/90000
a=rtcp-fb:101 nack
a=rtcp-fb:101 nack pli
a=rtcp-fb:101 goog-remb
a=fmtp:101 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f
a=rtpmap:102 rtx/90000
a=fmtp:102 apt=101
"""
)
VP8_SDP = lf2crlf(
    """a=rtpmap:97 VP8/90000
a=rtcp-fb:97 nack
a=rtcp-fb:97 nack pli
a=rtcp-fb:97 goog-remb
a=rtpmap:98 rtx/90000
a=fmtp:98 apt=97
"""
)


class BogusStreamTrack(AudioStreamTrack):
    kind = "bogus"


def mids(pc: RTCPeerConnection) -> list[Optional[str]]:
    mids = [x.mid for x in pc.getTransceivers()]
    if pc.sctp:
        mids.append(pc.sctp.mid)
    return sorted(mids)


def strip_ice_candidates(description: RTCSessionDescription) -> RTCSessionDescription:
    return RTCSessionDescription(
        sdp=STRIP_CANDIDATES_RE.sub("", description.sdp), type=description.type
    )


def track_states(pc: RTCPeerConnection) -> dict[str, list[str]]:
    states = {
        "connectionState": [pc.connectionState],
        "iceConnectionState": [pc.iceConnectionState],
        "iceGatheringState": [pc.iceGatheringState],
        "signalingState": [pc.signalingState],
    }

    @pc.on("connectionstatechange")
    def connectionstatechange() -> None:
        states["connectionState"].append(pc.connectionState)

    @pc.on("iceconnectionstatechange")
    def iceconnectionstatechange() -> None:
        states["iceConnectionState"].append(pc.iceConnectionState)

    @pc.on("icegatheringstatechange")
    def icegatheringstatechange() -> None:
        states["iceGatheringState"].append(pc.iceGatheringState)

    @pc.on("signalingstatechange")
    def signalingstatechange() -> None:
        states["signalingState"].append(pc.signalingState)

    return states


def track_remote_tracks(pc: RTCPeerConnection) -> list[MediaStreamTrack]:
    tracks: list[MediaStreamTrack] = []

    @pc.on("track")
    def track(track: MediaStreamTrack) -> None:
        tracks.append(track)

    return tracks


class RTCRtpCodecParametersTest(TestCase):
    def test_find_common_codecs_static(self) -> None:
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="audio/opus", clockRate=48000, channels=2, payloadType=96
            ),
            RTCRtpCodecParameters(
                mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
            ),
            RTCRtpCodecParameters(
                mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
            ),
        ]
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
            ),
            RTCRtpCodecParameters(
                mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
            ),
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(
            common,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
            ],
        )

    def find_common_codecs_dynamic(self) -> None:
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="audio/opus", clockRate=48000, channels=2, payloadType=96
            ),
            RTCRtpCodecParameters(
                mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
            ),
            RTCRtpCodecParameters(
                mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
            ),
        ]
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="audio/opus", clockRate=48000, channels=2, payloadType=100
            ),
            RTCRtpCodecParameters(
                mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
            ),
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(
            common,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/opus", clockRate=48000, channels=2, payloadType=100
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
            ],
        )

    def find_common_codecs_feedback(self) -> None:
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8",
                clockRate=90000,
                payloadType=100,
                rtcpFeedback=[
                    RTCRtcpFeedback(type="nack"),
                    RTCRtcpFeedback(type="nack", parameter="pli"),
                ],
            )
        ]
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8",
                clockRate=90000,
                payloadType=120,
                rtcpFeedback=[
                    RTCRtcpFeedback(type="nack"),
                    RTCRtcpFeedback(type="nack", parameter="sli"),
                ],
            )
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(len(common), 1)
        self.assertEqual(common[0].clockRate, 90000)
        self.assertEqual(common[0].name, "VP8")
        self.assertEqual(common[0].payloadType, 120)
        self.assertEqual(common[0].rtcpFeedback, [RTCRtcpFeedback(type="nack")])

    def test_find_common_codecs_rtx(self) -> None:
        local_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=100
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=101,
                parameters={"apt": 100},
            ),
        ]
        remote_codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=96
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=97,
                parameters={"apt": 96},
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP9", clockRate=90000, payloadType=98
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=99,
                parameters={"apt": 98},
            ),
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(
            common,
            [
                RTCRtpCodecParameters(
                    mimeType="video/VP8", clockRate=90000, payloadType=96
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=97,
                    parameters={"apt": 96},
                ),
            ],
        )

    def test_filter_preferred_codecs(self) -> None:
        codecs = [
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=100
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=101,
                parameters={"apt": 100},
            ),
            RTCRtpCodecParameters(
                mimeType="video/H264", clockRate=90000, payloadType=102
            ),
            RTCRtpCodecParameters(
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=103,
                parameters={"apt": 102},
            ),
        ]

        # no preferences
        self.assertEqual(filter_preferred_codecs(codecs, []), codecs)

        # with RTX, prefer VP8
        self.assertEqual(
            filter_preferred_codecs(
                codecs,
                [
                    RTCRtpCodecCapability(mimeType="video/VP8", clockRate=90000),
                    RTCRtpCodecCapability(mimeType="video/rtx", clockRate=90000),
                    RTCRtpCodecCapability(mimeType="video/H264", clockRate=90000),
                ],
            ),
            [
                RTCRtpCodecParameters(
                    mimeType="video/VP8", clockRate=90000, payloadType=100
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=101,
                    parameters={"apt": 100},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=103,
                    parameters={"apt": 102},
                ),
            ],
        )

        # with RTX, prefer H264
        self.assertEqual(
            filter_preferred_codecs(
                codecs,
                [
                    RTCRtpCodecCapability(mimeType="video/H264", clockRate=90000),
                    RTCRtpCodecCapability(mimeType="video/rtx", clockRate=90000),
                    RTCRtpCodecCapability(mimeType="video/VP8", clockRate=90000),
                ],
            ),
            [
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=103,
                    parameters={"apt": 102},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/VP8", clockRate=90000, payloadType=100
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=101,
                    parameters={"apt": 100},
                ),
            ],
        )

        # no RTX, same order
        self.assertEqual(
            filter_preferred_codecs(
                codecs,
                [
                    RTCRtpCodecCapability(mimeType="video/VP8", clockRate=90000),
                    RTCRtpCodecCapability(mimeType="video/H264", clockRate=90000),
                ],
            ),
            [
                RTCRtpCodecParameters(
                    mimeType="video/VP8", clockRate=90000, payloadType=100
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
            ],
        )

    def test_is_codec_compatible(self) -> None:
        # compatible: identical
        self.assertTrue(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
            )
        )
        self.assertTrue(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "packetization-mode": "0",
                        "profile-level-id": "42E01F",
                    },
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                ),
            )
        )

        # incompatible: different clockRate
        self.assertFalse(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=12345, payloadType=102
                ),
            )
        )

        # incompatible: different mimeType
        self.assertFalse(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264", clockRate=90000, payloadType=102
                ),
                RTCRtpCodecParameters(
                    mimeType="video/VP8", clockRate=90000, payloadType=102
                ),
            )
        )

        # incompatible: different H.264 profile
        self.assertFalse(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "packetization-mode": "1",
                        "profile-level-id": "42001f",
                    },
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "packetization-mode": "1",
                        "profile-level-id": "42e01f",
                    },
                ),
            )
        )

        # incompatible: different H.264 packetization mode
        self.assertFalse(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "packetization-mode": "0",
                        "profile-level-id": "42001f",
                    },
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "packetization-mode": "1",
                        "profile-level-id": "42001f",
                    },
                ),
            )
        )

        # incompatible: cannot parse H.264 profile
        self.assertFalse(
            is_codec_compatible(
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "profile-level-id": "42001f",
                    },
                ),
                RTCRtpCodecParameters(
                    mimeType="video/H264",
                    clockRate=90000,
                    payloadType=102,
                    parameters={
                        "profile-level-id": "blah",
                    },
                ),
            )
        )


class RTCPeerConnectionTest(TestCase):
    def assertBundled(self, pc: RTCPeerConnection) -> None:
        transceivers = pc.getTransceivers()
        self.assertEqual(
            transceivers[0].receiver.transport, transceivers[0].sender.transport
        )
        transport = transceivers[0].receiver.transport
        for i in range(1, len(transceivers)):
            self.assertEqual(transceivers[i].receiver.transport, transport)
            self.assertEqual(transceivers[i].sender.transport, transport)
        if pc.sctp:
            self.assertEqual(pc.sctp.transport, transport)

    def assertClosed(self, pc: RTCPeerConnection) -> None:
        self.assertEqual(pc.connectionState, "closed")
        self.assertEqual(pc.iceConnectionState, "closed")
        self.assertEqual(pc.signalingState, "closed")

    async def assertDataChannelOpen(self, dc: RTCDataChannel) -> None:
        await self.sleepWhile(lambda: dc.readyState == "connecting")
        self.assertEqual(dc.readyState, "open")

    async def assertIceChecking(self, pc: RTCPeerConnection) -> None:
        await self.sleepWhile(lambda: pc.iceConnectionState == "new")
        self.assertEqual(pc.iceConnectionState, "checking")
        self.assertEqual(pc.iceGatheringState, "complete")

    async def assertIceCompleted(
        self, pc1: RTCPeerConnection, pc2: RTCPeerConnection
    ) -> None:
        await self.sleepWhile(
            lambda: pc1.iceConnectionState == "checking"
            or pc2.iceConnectionState == "checking"
        )
        self.assertEqual(pc1.iceConnectionState, "completed")
        self.assertEqual(pc2.iceConnectionState, "completed")

    def assertHasIceCandidates(self, description: RTCSessionDescription) -> None:
        self.assertTrue("a=candidate:" in description.sdp)
        self.assertTrue("a=end-of-candidates" in description.sdp)

    def assertHasDtls(self, description: RTCSessionDescription, setup: str) -> None:
        self.assertTrue("a=fingerprint:sha-256" in description.sdp)
        self.assertEqual(
            set(re.findall("a=setup:(.*)\r$", description.sdp)), set([setup])
        )

    async def closeDataChannel(self, dc: RTCDataChannel) -> None:
        dc.close()
        await self.sleepWhile(lambda: dc.readyState == "closing")
        self.assertEqual(dc.readyState, "closed")

    async def sleepWhile(self, f: Callable[[], bool], max_sleep: float = 1.0) -> None:
        sleep = 0.1
        total = 0.0
        while f() and total < max_sleep:
            await asyncio.sleep(sleep)
            total += sleep

    def setUp(self) -> None:
        # save timers
        self.retry_max = aioice.stun.RETRY_MAX
        self.retry_rto = aioice.stun.RETRY_RTO

        # shorten timers to run tests faster
        aioice.stun.RETRY_MAX = 1
        aioice.stun.RETRY_RTO = 0.1

    def tearDown(self) -> None:
        # restore timers
        aioice.stun.RETRY_MAX = self.retry_max
        aioice.stun.RETRY_RTO = self.retry_rto

    @asynctest
    async def test_addIceCandidate(self) -> None:
        pc = RTCPeerConnection()
        pc.createDataChannel("test")
        offer = await pc.createOffer()
        await pc.setRemoteDescription(offer)
        self.assertFalse("a=candidate:" in pc.remoteDescription.sdp)
        candidate_with_index = RTCIceCandidate(
            component=1,
            foundation="0",
            ip="192.168.99.7",
            port=33543,
            priority=2122252543,
            protocol="UDP",
            type="host",
            sdpMLineIndex=0,
        )
        await pc.addIceCandidate(candidate_with_index)
        self.assertTrue("a=candidate:" in pc.remoteDescription.sdp)

        candidate_with_mid = RTCIceCandidate(
            component=1,
            foundation="0",
            ip="192.168.99.7",
            port=33544,
            priority=2122252543,
            protocol="UDP",
            type="host",
            sdpMid=pc.sctp.mid,
        )
        await pc.addIceCandidate(candidate_with_mid)
        self.assertEqual(pc.remoteDescription.sdp.count("a=candidate:"), 2)

    @asynctest
    async def test_addIceCandidate_no_sdpMid_or_sdpMLineIndex(self) -> None:
        pc = RTCPeerConnection()
        with self.assertRaises(ValueError) as cm:
            await pc.addIceCandidate(
                RTCIceCandidate(
                    component=1,
                    foundation="0",
                    ip="192.168.99.7",
                    port=33543,
                    priority=2122252543,
                    protocol="UDP",
                    type="host",
                )
            )
        self.assertEqual(
            str(cm.exception), "Candidate must have either sdpMid or sdpMLineIndex"
        )

    @asynctest
    async def test_addIceCandidate_null(self) -> None:
        pc = RTCPeerConnection()
        pc.createDataChannel("test")
        pc.addTransceiver("audio")
        pc.addTransceiver("video")
        offer = await pc.createOffer()
        await pc.setRemoteDescription(offer)
        self.assertFalse("a=end-of-candidates" in pc.remoteDescription.sdp)
        await pc.addIceCandidate(None)
        self.assertTrue("a=end-of-candidates" in pc.remoteDescription.sdp)

    @asynctest
    async def test_addIceCandidate_before_setremotedescription(self) -> None:
        pc = RTCPeerConnection()
        pc.createDataChannel("test")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        candidate_with_index = RTCIceCandidate(
            component=1,
            foundation="0",
            ip="192.168.99.7",
            port=33543,
            priority=2122252543,
            protocol="UDP",
            type="host",
            sdpMLineIndex=0,
        )
        with self.assertLogs("aiortc.rtcpeerconnection", level="WARN") as logger:
            await pc.addIceCandidate(candidate_with_index)
            self.assertEqual(
                logger.output,
                [
                    "WARNING:aiortc.rtcpeerconnection:RTCPeerConnection "
                    "addIceCandidate called without remote description"
                ],
            )

    @asynctest
    async def test_addTrack_audio(self) -> None:
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
        self.assertEqual(str(cm.exception), "Track already has a sender")

        # add another audio track
        track2 = AudioStreamTrack()
        sender2 = pc.addTrack(track2)
        self.assertIsNotNone(sender2)
        self.assertEqual(sender2.track, track2)
        self.assertEqual(pc.getSenders(), [sender1, sender2])
        self.assertEqual(len(pc.getTransceivers()), 2)

    @asynctest
    async def test_addTrack_bogus(self) -> None:
        pc = RTCPeerConnection()

        # try adding a bogus track
        with self.assertRaises(InternalError) as cm:
            pc.addTrack(BogusStreamTrack())
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    @asynctest
    async def test_addTrack_video(self) -> None:
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
        self.assertEqual(str(cm.exception), "Track already has a sender")

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

    @asynctest
    async def test_addTrack_closed(self) -> None:
        pc = RTCPeerConnection()
        await pc.close()
        with self.assertRaises(InvalidStateError) as cm:
            pc.addTrack(AudioStreamTrack())
        self.assertEqual(str(cm.exception), "RTCPeerConnection is closed")

    @asynctest
    async def test_addTransceiver_audio_inactive(self) -> None:
        pc = RTCPeerConnection()

        # add transceiver
        transceiver = pc.addTransceiver("audio", direction="inactive")
        self.assertIsNotNone(transceiver)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, "inactive")
        self.assertEqual(transceiver.sender.track, None)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver.sender])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # add track
        track = AudioStreamTrack()
        pc.addTrack(track)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, "sendonly")
        self.assertEqual(transceiver.sender.track, track)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(len(pc.getTransceivers()), 1)

        # stop transceiver
        await transceiver.stop()
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, "sendonly")
        self.assertEqual(transceiver.sender.track, track)
        self.assertEqual(transceiver.stopped, True)

    @asynctest
    async def test_addTransceiver_audio_sendrecv(self) -> None:
        pc = RTCPeerConnection()

        # add transceiver
        transceiver = pc.addTransceiver("audio")
        self.assertIsNotNone(transceiver)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, "sendrecv")
        self.assertEqual(transceiver.sender.track, None)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver.sender])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # add track
        track = AudioStreamTrack()
        pc.addTrack(track)
        self.assertEqual(transceiver.currentDirection, None)
        self.assertEqual(transceiver.direction, "sendrecv")
        self.assertEqual(transceiver.sender.track, track)
        self.assertEqual(transceiver.stopped, False)
        self.assertEqual(len(pc.getTransceivers()), 1)

    @asynctest
    async def test_addTransceiver_audio_track(self) -> None:
        pc = RTCPeerConnection()

        # add audio track
        track1 = AudioStreamTrack()
        transceiver1 = pc.addTransceiver(track1)
        self.assertIsNotNone(transceiver1)
        self.assertEqual(transceiver1.currentDirection, None)
        self.assertEqual(transceiver1.direction, "sendrecv")
        self.assertEqual(transceiver1.sender.track, track1)
        self.assertEqual(transceiver1.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver1.sender])
        self.assertEqual(len(pc.getTransceivers()), 1)

        # try to add same track again
        with self.assertRaises(InvalidAccessError) as cm:
            pc.addTransceiver(track1)
        self.assertEqual(str(cm.exception), "Track already has a sender")

        # add another audio track
        track2 = AudioStreamTrack()
        transceiver2 = pc.addTransceiver(track2)
        self.assertIsNotNone(transceiver2)
        self.assertEqual(transceiver2.currentDirection, None)
        self.assertEqual(transceiver2.direction, "sendrecv")
        self.assertEqual(transceiver2.sender.track, track2)
        self.assertEqual(transceiver2.stopped, False)
        self.assertEqual(pc.getSenders(), [transceiver1.sender, transceiver2.sender])
        self.assertEqual(len(pc.getTransceivers()), 2)

    def test_addTransceiver_bogus_direction(self) -> None:
        pc = RTCPeerConnection()

        # try adding a bogus kind
        with self.assertRaises(InternalError) as cm:
            pc.addTransceiver("audio", direction="bogus")
        self.assertEqual(str(cm.exception), 'Invalid direction "bogus"')

    def test_addTransceiver_bogus_kind(self) -> None:
        pc = RTCPeerConnection()

        # try adding a bogus kind
        with self.assertRaises(InternalError) as cm:
            pc.addTransceiver("bogus")
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    def test_addTransceiver_bogus_track(self) -> None:
        pc = RTCPeerConnection()

        # try adding a bogus track
        with self.assertRaises(InternalError) as cm:
            pc.addTransceiver(BogusStreamTrack())
        self.assertEqual(str(cm.exception), 'Invalid track kind "bogus"')

    @asynctest
    async def test_close(self) -> None:
        pc = RTCPeerConnection()
        pc_states = track_states(pc)

        # close once
        await pc.close()

        # close twice
        await pc.close()

        self.assertEqual(pc_states["signalingState"], ["stable", "closed"])

    async def _test_connect_audio_bidirectional(
        self, pc1: RTCPeerConnection, pc2: RTCPeerConnection
    ) -> None:
        pc1_states = track_states(pc1)
        pc1_tracks = track_remote_tracks(pc1)
        pc2_states = track_states(pc2)
        pc2_tracks = track_remote_tracks(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        track1 = AudioStreamTrack()
        pc1.addTrack(track1)
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue(
            lf2crlf(
                """a=rtpmap:96 opus/48000/2
a=rtpmap:9 G722/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
"""
            )
            in pc1.localDescription.sdp
        )
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # the RemoteStreamTrack should have the same ID as the source track
        self.assertEqual(len(pc2_tracks), 1)
        self.assertEqual(pc2_tracks[0].id, track1.id)

        # create answer
        track2 = AudioStreamTrack()
        pc2.addTrack(track2)
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue(
            lf2crlf(
                """a=rtpmap:96 opus/48000/2
a=rtpmap:9 G722/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
"""
            )
            in pc2.localDescription.sdp
        )
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "sendrecv")
        self.assertEqual(pc2.getTransceivers()[0].direction, "sendrecv")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "sendrecv")
        self.assertEqual(pc1.getTransceivers()[0].direction, "sendrecv")

        # the RemoteStreamTrack should have the same ID as the source track
        self.assertEqual(len(pc1_tracks), 1)
        self.assertEqual(pc1_tracks[0].id, track2.id)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # allow media to flow long enough to collect stats
        await asyncio.sleep(2)

        # check stats
        report = await pc1.getStats()
        self.assertIsInstance(report, RTCStatsReport)
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            [
                "inbound-rtp",
                "outbound-rtp",
                "remote-inbound-rtp",
                "remote-outbound-rtp",
                "transport",
            ],
        )

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_bidirectional(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_bidirectional(pc1, pc2)

    @asynctest
    async def test_connect_audio_bidirectional_with_empty_iceservers(self) -> None:
        pc1 = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_bidirectional(pc1, pc2)

    async def _test_connect_audio_bidirectional_trickle(self, with_mid: bool) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # strip out candidates
        desc1 = strip_ice_candidates(pc1.localDescription)

        # handle offer
        await pc2.setRemoteDescription(desc1)
        self.assertEqual(pc2.remoteDescription, desc1)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # strip out candidates
        desc2 = strip_ice_candidates(pc2.localDescription)

        # handle answer
        await pc1.setRemoteDescription(desc2)
        self.assertEqual(pc1.remoteDescription, desc2)

        # trickle candidates
        for transceiver in pc2.getTransceivers():
            iceGatherer = transceiver.sender.transport.transport.iceGatherer
            for candidate in iceGatherer.getLocalCandidates():
                if with_mid:
                    candidate.sdpMid = transceiver.mid
                else:
                    candidate.sdpMLineIndex = transceiver._get_mline_index()
                await pc1.addIceCandidate(candidate)
        for transceiver in pc1.getTransceivers():
            iceGatherer = transceiver.sender.transport.transport.iceGatherer
            for candidate in iceGatherer.getLocalCandidates():
                if with_mid:
                    candidate.sdpMid = transceiver.mid
                else:
                    candidate.sdpMLineIndex = transceiver._get_mline_index()
                await pc2.addIceCandidate(candidate)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_bidirectional_trickle_with_mid(self) -> None:
        await self._test_connect_audio_bidirectional_trickle(with_mid=True)

    @asynctest
    async def test_connect_audio_bidirectional_trickle_with_mline_index(
        self,
    ) -> None:
        await self._test_connect_audio_bidirectional_trickle(with_mid=False)

    @asynctest
    async def test_connect_audio_bidirectional_and_close(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        # create offer
        track1 = AudioStreamTrack()
        pc1.addTrack(track1)
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)

        # create answer
        track2 = AudioStreamTrack()
        pc2.addTrack(track2)
        answer = await pc2.createAnswer()
        await pc2.setLocalDescription(answer)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close one side, which causes the other to shutdown
        await pc1.close()
        await asyncio.sleep(1)

        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_codec_preferences_offerer(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # add track and set codec preferences to prefer PCMA / PCMU
        pc1.addTrack(AudioStreamTrack())
        capabilities = RTCRtpSender.getCapabilities("audio")
        preferences = list(filter(lambda x: x.name == "PCMA", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "PCMU", capabilities.codecs))
        transceiver = pc1.getTransceivers()[0]
        transceiver.setCodecPreferences(preferences)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue(
            lf2crlf(
                """a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
"""
            )
            in pc1.localDescription.sdp
        )
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue(
            lf2crlf(
                """a=rtpmap:8 PCMA/8000
a=rtpmap:0 PCMU/8000
"""
            )
            in pc2.localDescription.sdp
        )
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "sendrecv")
        self.assertEqual(pc2.getTransceivers()[0].direction, "sendrecv")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "sendrecv")
        self.assertEqual(pc1.getTransceivers()[0].direction, "sendrecv")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # allow media to flow long enough to collect stats
        await asyncio.sleep(2)

        # check stats
        report = await pc1.getStats()
        self.assertIsInstance(report, RTCStatsReport)
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            [
                "inbound-rtp",
                "outbound-rtp",
                "remote-inbound-rtp",
                "remote-outbound-rtp",
                "transport",
            ],
        )

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_mid_changes(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # add audio tracks immediately
        pc1.addTrack(AudioStreamTrack())
        pc2.addTrack(AudioStreamTrack())

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        # pretend we're Firefox!
        offer.sdp = offer.sdp.replace("a=mid:0", "a=mid:sdparta_0")

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["sdparta_0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")
        self.assertTrue("a=mid:sdparta_0" in pc1.localDescription.sdp)

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["sdparta_0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertTrue("a=mid:sdparta_0" in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_offer_recvonly_answer_recvonly(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTransceiver("audio", direction="recvonly")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=recvonly" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=inactive" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "inactive")
        self.assertEqual(pc2.getTransceivers()[0].direction, "recvonly")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "inactive")
        self.assertEqual(pc1.getTransceivers()[0].direction, "recvonly")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_offer_recvonly(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTransceiver("audio", direction="recvonly")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=recvonly" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=sendonly" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "sendonly")
        self.assertEqual(pc2.getTransceivers()[0].direction, "sendrecv")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "recvonly")
        self.assertEqual(pc1.getTransceivers()[0].direction, "recvonly")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_offer_sendonly(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTransceiver(AudioStreamTrack(), direction="sendonly")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=sendonly" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=recvonly" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "recvonly")
        self.assertEqual(pc2.getTransceivers()[0].direction, "recvonly")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "sendonly")
        self.assertEqual(pc1.getTransceivers()[0].direction, "sendonly")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_offer_sendrecv_answer_recvonly(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=recvonly" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "recvonly")
        self.assertEqual(pc2.getTransceivers()[0].direction, "recvonly")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "sendonly")
        self.assertEqual(pc1.getTransceivers()[0].direction, "sendrecv")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_offer_sendrecv_answer_sendonly(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        pc2.getTransceivers()[0].direction = "sendonly"
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=sendonly" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "sendonly")
        self.assertEqual(pc2.getTransceivers()[0].direction, "sendonly")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "recvonly")
        self.assertEqual(pc1.getTransceivers()[0].direction, "sendrecv")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_two_tracks(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0", "1"])
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    async def _test_connect_audio_and_video(
        self, pc1: RTCPeerConnection, pc2: RTCPeerConnection
    ) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.connectionState, "new")
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.connectionState, "new")
        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertTrue("m=video " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=video " in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=video " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_and_video(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_and_video(pc1, pc2)

    @asynctest
    async def test_connect_audio_and_video_bundlepolicy_max_compat(self) -> None:
        pc1 = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_COMPAT)
        )
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_and_video(pc1, pc2)

    @asynctest
    async def test_connect_audio_and_video_bundlepolicy_max_bundle(self) -> None:
        pc1 = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)
        )
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_and_video(pc1, pc2)

    async def _test_connect_audio_and_video_mediaplayer(
        self, stop_tracks: bool
    ) -> None:
        """
        Negotiate bidirectional audio + video, with one party reading media from a file.

        We can optionally stop the media tracks before closing the peer connections.
        """
        media_test = MediaTestCase()
        media_test.setUp()
        media_path = media_test.create_audio_and_video_file(name="test.mp4", duration=5)
        player = MediaPlayer(media_path)

        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(player.audio)
        pc1.addTrack(player.video)
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertTrue("m=video " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=video " in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=video " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

        # let media flow
        await asyncio.sleep(1)

        # stop tracks
        if stop_tracks:
            player.audio.stop()
            player.video.stop()

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )
        media_test.tearDown()

    @asynctest
    async def test_connect_audio_and_video_mediaplayer(self) -> None:
        await self._test_connect_audio_and_video_mediaplayer(stop_tracks=False)

    @asynctest
    async def test_connect_audio_and_video_mediaplayer_stop_tracks(self) -> None:
        await self._test_connect_audio_and_video_mediaplayer(stop_tracks=True)

    async def _test_connect_audio_and_video_and_data_channel(
        self, pc1: RTCPeerConnection, pc2: RTCPeerConnection
    ) -> None:
        pc1_states = track_states(pc1)
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())
        pc1.createDataChannel("chat", protocol="bob")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertTrue("m=video " in offer.sdp)
        self.assertTrue("m=application " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1", "2"])

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ["0", "1", "2"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=video " in answer.sdp)
        self.assertTrue("m=application " in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue("m=application " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_and_video_and_data_channel(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_and_video_and_data_channel(pc1, pc2)

    @asynctest
    async def test_connect_audio_and_video_and_data_channel_max_bundle(self) -> None:
        pc1 = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)
        )
        pc2 = RTCPeerConnection()
        await self._test_connect_audio_and_video_and_data_channel(pc1, pc2)

    @asynctest
    async def test_connect_audio_and_video_and_data_channel_ice_fail(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())
        pc1.createDataChannel("chat", protocol="bob")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertTrue("m=video " in offer.sdp)
        self.assertTrue("m=application " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1", "2"])

        # close one side
        pc1_description = pc1.localDescription
        await pc1.close()

        # handle offer
        await pc2.setRemoteDescription(pc1_description)
        self.assertEqual(pc2.remoteDescription, pc1_description)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ["0", "1", "2"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=video " in answer.sdp)
        self.assertTrue("m=application " in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue("m=application " in pc2.localDescription.sdp)

        # check outcome
        done = asyncio.Event()

        @pc2.on("iceconnectionstatechange")
        def iceconnectionstatechange() -> None:
            done.set()

        await done.wait()
        self.assertEqual(pc1.iceConnectionState, "closed")
        self.assertEqual(pc2.iceConnectionState, "failed")

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(pc1_states["connectionState"], ["new", "closed"])
        self.assertEqual(pc1_states["iceConnectionState"], ["new", "closed"])
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"], ["stable", "have-local-offer", "closed"]
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "failed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "failed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_audio_then_video(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # 1. AUDIO ONLY

        # create offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertFalse("m=video " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertFalse("m=video " in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertFalse("m=video " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

        # 2. ADD VIDEO

        # create offer
        pc1.addTrack(VideoStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=audio " in offer.sdp)
        self.assertTrue("m=video " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 2)
        self.assertEqual(len(pc2.getSenders()), 2)
        self.assertEqual(len(pc2.getTransceivers()), 2)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=video " in answer.sdp)

        await pc2.setLocalDescription(answer)
        self.assertEqual(pc2.iceConnectionState, "completed")
        self.assertEqual(pc2.iceGatheringState, "complete")
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=video " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, "completed")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"],
            ["new", "connecting", "connected", "connecting", "connected", "closed"],
        )
        self.assertEqual(
            pc1_states["iceConnectionState"],
            ["new", "checking", "completed", "new", "completed", "closed"],
        )
        self.assertEqual(
            pc1_states["iceGatheringState"],
            ["new", "gathering", "complete", "new", "gathering", "complete"],
        )
        self.assertEqual(
            pc1_states["signalingState"],
            [
                "stable",
                "have-local-offer",
                "stable",
                "have-local-offer",
                "stable",
                "closed",
            ],
        )

        self.assertEqual(
            pc2_states["connectionState"],
            ["new", "connecting", "connected", "connecting", "connected", "closed"],
        )
        self.assertEqual(
            pc2_states["iceConnectionState"],
            ["new", "checking", "completed", "new", "completed", "closed"],
        )
        self.assertEqual(
            pc2_states["iceGatheringState"],
            ["new", "gathering", "complete", "new", "complete"],
        )
        self.assertEqual(
            pc2_states["signalingState"],
            [
                "stable",
                "have-remote-offer",
                "stable",
                "have-remote-offer",
                "stable",
                "closed",
            ],
        )

    @asynctest
    async def test_connect_video_bidirectional(self) -> None:
        VIDEO_SDP = VP8_SDP + H264_SDP

        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(VideoStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=video " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=video " in pc1.localDescription.sdp)
        self.assertTrue(VIDEO_SDP in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=video " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue(VIDEO_SDP in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # let media flow to trigger RTCP feedback, including REMB
        await asyncio.sleep(5)

        # check stats
        report = await pc1.getStats()
        self.assertIsInstance(report, RTCStatsReport)
        self.assertEqual(
            sorted([s.type for s in report.values()]),
            [
                "inbound-rtp",
                "outbound-rtp",
                "remote-inbound-rtp",
                "remote-outbound-rtp",
                "transport",
            ],
        )

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_video_h264(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(VideoStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=video " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=video " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # strip out vp8
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[0].rtp.codecs.pop(0)
        parsed.media[0].fmt.pop(0)
        desc1 = RTCSessionDescription(sdp=str(parsed), type=pc1.localDescription.type)
        self.assertFalse("VP8" in desc1.sdp)
        self.assertTrue("H264" in desc1.sdp)

        # handle offer
        await pc2.setRemoteDescription(desc1)
        self.assertEqual(pc2.remoteDescription, desc1)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=video " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_video_no_ssrc(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.addTrack(VideoStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=video " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=video " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # strip out SSRC
        mangled = RTCSessionDescription(
            sdp=re.sub("^a=ssrc:.*\r\n", "", pc1.localDescription.sdp, flags=re.M),
            type=pc1.localDescription.type,
        )

        # handle offer
        await pc2.setRemoteDescription(mangled)
        self.assertEqual(pc2.remoteDescription, mangled)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=video " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_video_codec_preferences_offerer(self) -> None:
        VIDEO_SDP = H264_SDP + VP8_SDP

        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # add track and set codec preferences to prefer H264
        pc1.addTrack(VideoStreamTrack())
        capabilities = RTCRtpSender.getCapabilities("video")
        preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "VP8", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
        transceiver = pc1.getTransceivers()[0]
        transceiver.setCodecPreferences(preferences)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=video " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=video " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")
        self.assertTrue(VIDEO_SDP in pc1.localDescription.sdp)

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=video " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertTrue(VIDEO_SDP in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_video_codec_preferences_offerer_only_h264(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # add track and set codec preferences to only allow H264
        pc1.addTrack(VideoStreamTrack())
        capabilities = RTCRtpSender.getCapabilities("video")
        preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
        preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
        transceiver = pc1.getTransceivers()[0]
        transceiver.setCodecPreferences(preferences)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=video " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=video " in pc1.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")
        self.assertFalse("VP8" in pc1.localDescription.sdp)

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        pc2.addTrack(VideoStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=video " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=video " in pc2.localDescription.sdp)
        self.assertTrue("a=sendrecv" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")
        self.assertFalse("VP8" in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_and_close_immediately(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # create two data channels
        dc1 = pc1.createDataChannel("chat1")
        self.assertEqual(dc1.readyState, "connecting")
        dc2 = pc1.createDataChannel("chat2")
        self.assertEqual(dc2.readyState, "connecting")

        # close one data channel
        dc1.close()
        self.assertEqual(dc1.readyState, "closed")
        self.assertEqual(dc2.readyState, "connecting")

        # perform SDP exchange
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        self.assertEqual(dc1.readyState, "closed")
        await self.assertDataChannelOpen(dc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

    @asynctest
    async def test_connect_datachannel_negotiated_and_close_immediately(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # create two negotiated data channels
        dc1 = pc1.createDataChannel("chat1", negotiated=True, id=100)
        self.assertEqual(dc1.readyState, "connecting")
        dc2 = pc1.createDataChannel("chat2", negotiated=True, id=102)
        self.assertEqual(dc2.readyState, "connecting")

        # close one data channel
        dc1.close()
        self.assertEqual(dc1.readyState, "closed")
        self.assertEqual(dc2.readyState, "connecting")

        # perform SDP exchange
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        self.assertEqual(dc1.readyState, "closed")
        await self.assertDataChannelOpen(dc2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

    @asynctest
    async def test_connect_datachannel_legacy_sdp(self) -> None:
        pc1 = RTCPeerConnection()
        pc1._sctpLegacySdp = True
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send("string-echo: " + message)
                else:
                    channel.send(b"binary-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, None)
        self.assertEqual(dc.maxRetransmits, None)
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send messages
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")
            dc.send("")
            dc.send(b"\x00\x01\x02\x03")
            dc.send(b"")
            dc.send(LONG_DATA)
            with self.assertRaises(ValueError) as cm:
                dc.send(1234)  # type: ignore
            self.assertEqual(
                str(cm.exception), "Cannot send unsupported data type: <class 'int'>"
            )
            self.assertEqual(dc.bufferedAmount, 2011)

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertTrue(
            "a=sctpmap:5000 webrtc-datachannel 65535" in pc1.localDescription.sdp
        )
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertTrue(
            "a=sctpmap:5000 webrtc-datachannel 65535" in pc2.localDescription.sdp
        )
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)
        self.assertEqual(dc.bufferedAmount, 0)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, None)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, None)
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got messages
        await asyncio.sleep(0.1)
        self.assertEqual(
            pc2_data_messages, ["hello", "", b"\x00\x01\x02\x03", b"", LONG_DATA]
        )

        # check pc1 got replies
        self.assertEqual(
            pc1_data_messages,
            [
                "string-echo: hello",
                "string-echo: ",
                b"binary-echo: \x00\x01\x02\x03",
                b"binary-echo: ",
                b"binary-echo: " + LONG_DATA,
            ],
        )

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_modern_sdp(self) -> None:
        pc1 = RTCPeerConnection()
        pc1._sctpLegacySdp = False
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send("string-echo: " + message)
                else:
                    channel.send(b"binary-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, None)
        self.assertEqual(dc.maxRetransmits, None)
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send messages
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")
            dc.send("")
            dc.send(b"\x00\x01\x02\x03")
            dc.send(b"")
            dc.send(LONG_DATA)
            with self.assertRaises(ValueError) as cm:
                dc.send(1234)  # type: ignore
            self.assertEqual(
                str(cm.exception), "Cannot send unsupported data type: <class 'int'>"
            )

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertTrue("a=sctp-port:5000" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertTrue("a=sctp-port:5000" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, None)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, None)
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got messages
        await asyncio.sleep(0.1)
        self.assertEqual(
            pc2_data_messages, ["hello", "", b"\x00\x01\x02\x03", b"", LONG_DATA]
        )

        # check pc1 got replies
        self.assertEqual(
            pc1_data_messages,
            [
                "string-echo: hello",
                "string-echo: ",
                b"binary-echo: \x00\x01\x02\x03",
                b"binary-echo: ",
                b"binary-echo: " + LONG_DATA,
            ],
        )

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_modern_sdp_negotiated(self) -> None:
        pc1 = RTCPeerConnection()
        pc1._sctpLegacySdp = False
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        # create data channels
        dc1 = pc1.createDataChannel("chat", protocol="bob", negotiated=True, id=100)
        self.assertEqual(dc1.id, 100)
        self.assertEqual(dc1.label, "chat")
        self.assertEqual(dc1.maxPacketLifeTime, None)
        self.assertEqual(dc1.maxRetransmits, None)
        self.assertEqual(dc1.ordered, True)
        self.assertEqual(dc1.protocol, "bob")
        self.assertEqual(dc1.readyState, "connecting")

        dc2 = pc2.createDataChannel("chat", protocol="bob", negotiated=True, id=100)
        self.assertEqual(dc2.id, 100)
        self.assertEqual(dc2.label, "chat")
        self.assertEqual(dc2.maxPacketLifeTime, None)
        self.assertEqual(dc2.maxRetransmits, None)
        self.assertEqual(dc2.ordered, True)
        self.assertEqual(dc2.protocol, "bob")
        self.assertEqual(dc2.readyState, "connecting")

        @dc1.on("message")
        def on_message1(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        @dc2.on("message")
        def on_message2(message: Union[bytes, str]) -> None:
            pc2_data_messages.append(message)
            if isinstance(message, str):
                dc2.send("string-echo: " + message)
            else:
                dc2.send(b"binary-echo: " + message)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertTrue("a=sctp-port:5000" in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertTrue("a=sctp-port:5000" in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc1)
        await self.assertDataChannelOpen(dc2)

        # send message
        dc1.send("hello")
        dc1.send("")
        dc1.send(b"\x00\x01\x02\x03")
        dc1.send(b"")
        dc1.send(LONG_DATA)
        with self.assertRaises(ValueError) as cm:
            dc1.send(1234)  # type: ignore
        self.assertEqual(
            str(cm.exception), "Cannot send unsupported data type: <class 'int'>"
        )

        # check pc2 got messages
        await asyncio.sleep(0.1)
        self.assertEqual(
            pc2_data_messages, ["hello", "", b"\x00\x01\x02\x03", b"", LONG_DATA]
        )

        # check pc1 got replies
        self.assertEqual(
            pc1_data_messages,
            [
                "string-echo: hello",
                "string-echo: ",
                b"binary-echo: \x00\x01\x02\x03",
                b"binary-echo: ",
                b"binary-echo: " + LONG_DATA,
            ],
        )

        # close data channels
        await self.closeDataChannel(dc1)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_recycle_stream_id(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # create three data channels
        dc1 = pc1.createDataChannel("chat1")
        self.assertEqual(dc1.readyState, "connecting")
        dc2 = pc1.createDataChannel("chat2")
        self.assertEqual(dc2.readyState, "connecting")
        dc3 = pc1.createDataChannel("chat3")
        self.assertEqual(dc3.readyState, "connecting")

        # perform SDP exchange
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc1)
        self.assertEqual(dc1.id, 1)
        await self.assertDataChannelOpen(dc2)
        self.assertEqual(dc2.id, 3)
        await self.assertDataChannelOpen(dc3)
        self.assertEqual(dc3.id, 5)

        # close one data channel
        await self.closeDataChannel(dc2)

        # create a new data channel
        dc4 = pc1.createDataChannel("chat4")
        await self.assertDataChannelOpen(dc4)
        self.assertEqual(dc4.id, 3)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

    def test_create_datachannel_with_maxpacketlifetime_and_maxretransmits(self) -> None:
        pc = RTCPeerConnection()
        with self.assertRaises(ValueError) as cm:
            pc.createDataChannel("chat", maxPacketLifeTime=500, maxRetransmits=0)
        self.assertEqual(
            str(cm.exception),
            "Cannot specify both maxPacketLifeTime and maxRetransmits",
        )

    @asynctest
    async def test_datachannel_bufferedamountlowthreshold(self) -> None:
        pc = RTCPeerConnection()
        dc = pc.createDataChannel("chat")
        self.assertEqual(dc.bufferedAmountLowThreshold, 0)

        dc.bufferedAmountLowThreshold = 4294967295
        self.assertEqual(dc.bufferedAmountLowThreshold, 4294967295)

        dc.bufferedAmountLowThreshold = 16384
        self.assertEqual(dc.bufferedAmountLowThreshold, 16384)

        dc.bufferedAmountLowThreshold = 0
        self.assertEqual(dc.bufferedAmountLowThreshold, 0)

        with self.assertRaises(ValueError):
            dc.bufferedAmountLowThreshold = -1
            self.assertEqual(dc.bufferedAmountLowThreshold, 0)

        with self.assertRaises(ValueError):
            dc.bufferedAmountLowThreshold = 4294967296
            self.assertEqual(dc.bufferedAmountLowThreshold, 0)

    @asynctest
    async def test_datachannel_send_invalid_state(self) -> None:
        pc = RTCPeerConnection()
        dc = pc.createDataChannel("chat")
        with self.assertRaises(InvalidStateError):
            dc.send("hello")

    @asynctest
    async def test_connect_datachannel_then_audio(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send("string-echo: " + message)
                else:
                    channel.send(b"binary-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, None)
        self.assertEqual(dc.maxRetransmits, None)
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send messages
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")
            dc.send("")
            dc.send(b"\x00\x01\x02\x03")
            dc.send(b"")
            dc.send(LONG_DATA)
            with self.assertRaises(ValueError) as cm:
                dc.send(1234)  # type: ignore
            self.assertEqual(
                str(cm.exception), "Cannot send unsupported data type: <class 'int'>"
            )

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # 1. DATA CHANNEL ONLY

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, None)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, None)
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got messages
        await asyncio.sleep(0.1)
        self.assertEqual(
            pc2_data_messages, ["hello", "", b"\x00\x01\x02\x03", b"", LONG_DATA]
        )

        # check pc1 got replies
        self.assertEqual(
            pc1_data_messages,
            [
                "string-echo: hello",
                "string-echo: ",
                b"binary-echo: \x00\x01\x02\x03",
                b"binary-echo: ",
                b"binary-echo: " + LONG_DATA,
            ],
        )

        # 2. ADD AUDIO

        # create offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertTrue("m=audio " in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        pc2.addTrack(AudioStreamTrack())
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertTrue("m=audio " in answer.sdp)

        await pc2.setLocalDescription(answer)
        self.assertEqual(pc2.iceConnectionState, "completed")
        self.assertEqual(pc2.iceGatheringState, "complete")
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, "completed")

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # check a single transport is used
        self.assertBundled(pc1)
        self.assertBundled(pc2)

        # 3. CLEANUP

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"],
            ["new", "connecting", "connected", "connecting", "connected", "closed"],
        )
        self.assertEqual(
            pc1_states["iceConnectionState"],
            ["new", "checking", "completed", "new", "completed", "closed"],
        )
        self.assertEqual(
            pc1_states["iceGatheringState"],
            ["new", "gathering", "complete", "new", "gathering", "complete"],
        )
        self.assertEqual(
            pc1_states["signalingState"],
            [
                "stable",
                "have-local-offer",
                "stable",
                "have-local-offer",
                "stable",
                "closed",
            ],
        )

        self.assertEqual(
            pc2_states["connectionState"],
            ["new", "connecting", "connected", "connecting", "connected", "closed"],
        )
        self.assertEqual(
            pc2_states["iceConnectionState"],
            ["new", "checking", "completed", "new", "completed", "closed"],
        )
        self.assertEqual(
            pc2_states["iceGatheringState"],
            ["new", "gathering", "complete", "new", "complete"],
        )
        self.assertEqual(
            pc2_states["signalingState"],
            [
                "stable",
                "have-remote-offer",
                "stable",
                "have-remote-offer",
                "stable",
                "closed",
            ],
        )

    async def _test_connect_datachannel_trickle(self, with_mid: bool) -> None:
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                pc2_data_messages.append(message)
                if isinstance(message, str):
                    channel.send("string-echo: " + message)
                else:
                    channel.send(b"binary-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, None)
        self.assertEqual(dc.maxRetransmits, None)
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send messages
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")
            dc.send("")
            dc.send(b"\x00\x01\x02\x03")
            dc.send(b"")
            dc.send(LONG_DATA)
            with self.assertRaises(ValueError) as cm:
                dc.send(1234)  # type: ignore
            self.assertEqual(
                str(cm.exception), "Cannot send unsupported data type: <class 'int'>"
            )

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # strip out candidates
        desc1 = strip_ice_candidates(pc1.localDescription)

        # handle offer
        await pc2.setRemoteDescription(desc1)
        self.assertEqual(pc2.remoteDescription, desc1)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # strip out candidates
        desc2 = strip_ice_candidates(pc2.localDescription)

        # handle answer
        await pc1.setRemoteDescription(desc2)
        self.assertEqual(pc1.remoteDescription, desc2)

        # trickle candidates
        for candidate in pc2.sctp.transport.transport.iceGatherer.getLocalCandidates():
            if with_mid:
                candidate.sdpMid = pc2.sctp.mid
            else:
                candidate.sdpMLineIndex = 0
            await pc1.addIceCandidate(candidate)
        for candidate in pc1.sctp.transport.transport.iceGatherer.getLocalCandidates():
            if with_mid:
                candidate.sdpMid = pc1.sctp.mid
            else:
                candidate.sdpMLineIndex = 0
            await pc2.addIceCandidate(candidate)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, None)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, None)
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got messages
        await asyncio.sleep(0.1)
        self.assertEqual(
            pc2_data_messages, ["hello", "", b"\x00\x01\x02\x03", b"", LONG_DATA]
        )

        # check pc1 got replies
        self.assertEqual(
            pc1_data_messages,
            [
                "string-echo: hello",
                "string-echo: ",
                b"binary-echo: \x00\x01\x02\x03",
                b"binary-echo: ",
                b"binary-echo: " + LONG_DATA,
            ],
        )

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_trickle_with_mid(self) -> None:
        await self._test_connect_datachannel_trickle(with_mid=True)

    @asynctest
    async def test_connect_datachannel_trickle_with_mline_index(self) -> None:
        await self._test_connect_datachannel_trickle(with_mid=False)

    @asynctest
    async def test_connect_datachannel_max_packet_lifetime(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                assert isinstance(message, str)
                pc2_data_messages.append(message)
                channel.send("string-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", maxPacketLifeTime=500, protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, 500)
        self.assertEqual(dc.maxRetransmits, None)
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send message
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # create offer
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)
        await pc2.setRemoteDescription(pc1.localDescription)

        # create answer
        answer = await pc2.createAnswer()
        await pc2.setLocalDescription(answer)
        await pc1.setRemoteDescription(pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, 500)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, None)
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got message
        await asyncio.sleep(0.1)
        self.assertEqual(pc2_data_messages, ["hello"])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, ["string-echo: hello"])

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_max_retransmits(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                assert isinstance(message, str)
                pc2_data_messages.append(message)
                channel.send("string-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", maxRetransmits=0, protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, None)
        self.assertEqual(dc.maxRetransmits, 0)
        self.assertEqual(dc.ordered, True)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send message
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # create offer
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)
        await pc2.setRemoteDescription(pc1.localDescription)

        # create answer
        answer = await pc2.createAnswer()
        await pc2.setLocalDescription(answer)
        await pc1.setRemoteDescription(pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, None)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, 0)
        self.assertEqual(pc2_data_channels[0].ordered, True)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got message
        await asyncio.sleep(0.1)
        self.assertEqual(pc2_data_messages, ["hello"])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, ["string-echo: hello"])

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_connect_datachannel_unordered(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_data_messages = []
        pc1_states = track_states(pc1)

        pc2 = RTCPeerConnection()
        pc2_data_channels = []
        pc2_data_messages = []
        pc2_states = track_states(pc2)

        @pc2.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.assertEqual(channel.readyState, "open")
            pc2_data_channels.append(channel)

            @channel.on("message")
            def on_message(message: Union[bytes, str]) -> None:
                assert isinstance(message, str)
                pc2_data_messages.append(message)
                channel.send("string-echo: " + message)

        # create data channel
        dc = pc1.createDataChannel("chat", ordered=False, protocol="bob")
        self.assertEqual(dc.label, "chat")
        self.assertEqual(dc.maxPacketLifeTime, None)
        self.assertEqual(dc.maxRetransmits, None)
        self.assertEqual(dc.ordered, False)
        self.assertEqual(dc.protocol, "bob")
        self.assertEqual(dc.readyState, "connecting")

        # send message
        @dc.on("open")
        def on_open() -> None:
            dc.send("hello")

        @dc.on("message")
        def on_message(message: Union[bytes, str]) -> None:
            pc1_data_messages.append(message)

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")
        self.assertTrue("m=application " in offer.sdp)
        self.assertFalse("a=candidate:" in offer.sdp)
        self.assertFalse("a=end-of-candidates" in offer.sdp)

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0"])
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertHasIceCandidates(pc1.localDescription)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 0)
        self.assertEqual(len(pc2.getSenders()), 0)
        self.assertEqual(len(pc2.getTransceivers()), 0)
        self.assertEqual(mids(pc2), ["0"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("m=application " in answer.sdp)
        self.assertFalse("a=candidate:" in answer.sdp)
        self.assertFalse("a=end-of-candidates" in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertHasIceCandidates(pc2.localDescription)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)
        await self.assertDataChannelOpen(dc)

        # check pc2 got a datachannel
        self.assertEqual(len(pc2_data_channels), 1)
        self.assertEqual(pc2_data_channels[0].label, "chat")
        self.assertEqual(pc2_data_channels[0].maxPacketLifeTime, None)
        self.assertEqual(pc2_data_channels[0].maxRetransmits, None)
        self.assertEqual(pc2_data_channels[0].ordered, False)
        self.assertEqual(pc2_data_channels[0].protocol, "bob")

        # check pc2 got message
        await asyncio.sleep(0.1)
        self.assertEqual(pc2_data_messages, ["hello"])

        # check pc1 got replies
        self.assertEqual(pc1_data_messages, ["string-echo: hello"])

        # close data channel
        await self.closeDataChannel(dc)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            ["stable", "have-local-offer", "stable", "closed"],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            ["stable", "have-remote-offer", "stable", "closed"],
        )

    @asynctest
    async def test_createAnswer_closed(self) -> None:
        pc = RTCPeerConnection()
        await pc.close()
        with self.assertRaises(InvalidStateError) as cm:
            await pc.createAnswer()
        self.assertEqual(str(cm.exception), "RTCPeerConnection is closed")

    @asynctest
    async def test_createAnswer_without_offer(self) -> None:
        pc = RTCPeerConnection()
        with self.assertRaises(InvalidStateError) as cm:
            await pc.createAnswer()
        self.assertEqual(
            str(cm.exception), 'Cannot create answer in signaling state "stable"'
        )

    @asynctest
    async def test_createOffer_closed(self) -> None:
        pc = RTCPeerConnection()
        await pc.close()
        with self.assertRaises(InvalidStateError) as cm:
            await pc.createOffer()
        self.assertEqual(str(cm.exception), "RTCPeerConnection is closed")

    @asynctest
    async def test_createOffer_without_media(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)
        await pc2.setRemoteDescription(offer)

        answer = await pc2.createAnswer()
        await pc2.setLocalDescription(answer)
        await pc1.setRemoteDescription(answer)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setLocalDescription_implicit(self) -> None:
        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())
        offer = await pc.createOffer()
        await pc.setRemoteDescription(offer)

        await pc.setLocalDescription()
        self.assertEqual(pc.localDescription.type, "answer")

        await pc.setLocalDescription()
        self.assertEqual(pc.localDescription.type, "offer")

        await pc.close()
        with self.assertRaises(InvalidStateError) as cm:
            await pc.setLocalDescription()
        self.assertEqual(str(cm.exception), "RTCPeerConnection is closed")

    @asynctest
    async def test_setLocalDescription_unexpected_answer(self) -> None:
        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())
        answer = await pc.createOffer()
        answer.type = "answer"
        with self.assertRaises(InvalidStateError) as cm:
            await pc.setLocalDescription(answer)
        self.assertEqual(
            str(cm.exception), 'Cannot handle answer in signaling state "stable"'
        )

        # close
        await pc.close()

    @asynctest
    async def test_setLocalDescription_unexpected_offer(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # apply offer
        pc1.addTrack(AudioStreamTrack())
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)

        # mangle answer into an offer
        offer = pc2.remoteDescription
        offer.type = "offer"
        with self.assertRaises(InvalidStateError) as cm:
            await pc2.setLocalDescription(offer)
        self.assertEqual(
            str(cm.exception),
            'Cannot handle offer in signaling state "have-remote-offer"',
        )

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_no_common_audio(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()

        mangled_sdp = []
        for line in offer.sdp.split("\n"):
            if line.startswith("a=rtpmap:"):
                continue
            mangled_sdp.append(line)

        mangled = RTCSessionDescription(sdp="\n".join(mangled_sdp), type=offer.type)

        with self.assertRaises(OperationError) as cm:
            await pc2.setRemoteDescription(mangled)
        self.assertEqual(
            str(cm.exception), "Failed to set remote audio description send parameters"
        )

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_no_common_video(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        pc1.addTrack(VideoStreamTrack())
        offer = await pc1.createOffer()

        mangled = RTCSessionDescription(
            sdp=offer.sdp.replace("90000", "92000"),
            type=offer.type,
        )
        with self.assertRaises(OperationError) as cm:
            await pc2.setRemoteDescription(mangled)
        self.assertEqual(
            str(cm.exception), "Failed to set remote video description send parameters"
        )

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_media_mismatch(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # apply offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)
        await pc2.setRemoteDescription(pc1.localDescription)

        # apply answer
        answer = await pc2.createAnswer()
        await pc2.setLocalDescription(answer)
        mangled = RTCSessionDescription(
            sdp=pc2.localDescription.sdp.replace("m=audio", "m=video"),
            type=pc2.localDescription.type,
        )
        with self.assertRaises(ValueError) as cm:
            await pc1.setRemoteDescription(mangled)
        self.assertEqual(
            str(cm.exception), "Media sections in answer do not match offer"
        )

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_with_invalid_dtls_setup_for_answer(
        self,
    ) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # apply offer
        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)
        await pc2.setRemoteDescription(pc1.localDescription)

        # apply answer
        answer = await pc2.createAnswer()
        await pc2.setLocalDescription(answer)
        mangled = RTCSessionDescription(
            sdp=pc2.localDescription.sdp.replace("a=setup:active", "a=setup:actpass"),
            type=pc2.localDescription.type,
        )
        with self.assertRaises(ValueError) as cm:
            await pc1.setRemoteDescription(mangled)
        self.assertEqual(
            str(cm.exception),
            "DTLS setup attribute must be 'active' or 'passive' for an answer",
        )

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_without_ice_credentials(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)

        mangled = RTCSessionDescription(
            sdp=re.sub(
                "^a=(ice-ufrag|ice-pwd):.*\r\n",
                "",
                pc1.localDescription.sdp,
                flags=re.M,
            ),
            type=pc1.localDescription.type,
        )
        with self.assertRaises(ValueError) as cm:
            await pc2.setRemoteDescription(mangled)
        self.assertEqual(
            str(cm.exception), "ICE username fragment or password is missing"
        )

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_without_rtcp_mux(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        pc1.addTrack(AudioStreamTrack())
        offer = await pc1.createOffer()
        await pc1.setLocalDescription(offer)

        mangled = RTCSessionDescription(
            sdp=re.sub("^a=rtcp-mux\r\n", "", pc1.localDescription.sdp, flags=re.M),
            type=pc1.localDescription.type,
        )
        with self.assertRaises(ValueError) as cm:
            await pc2.setRemoteDescription(mangled)
        self.assertEqual(str(cm.exception), "RTCP mux is not enabled")

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_unexpected_answer(self) -> None:
        pc = RTCPeerConnection()
        with self.assertRaises(InvalidStateError) as cm:
            await pc.setRemoteDescription(RTCSessionDescription(sdp="", type="answer"))
        self.assertEqual(
            str(cm.exception), 'Cannot handle answer in signaling state "stable"'
        )

        # close
        await pc.close()

    @asynctest
    async def test_setRemoteDescription_unexpected_offer(self) -> None:
        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        with self.assertRaises(InvalidStateError) as cm:
            await pc.setRemoteDescription(RTCSessionDescription(sdp="", type="offer"))
        self.assertEqual(
            str(cm.exception),
            'Cannot handle offer in signaling state "have-local-offer"',
        )

        # close
        await pc.close()

    @asynctest
    async def test_setRemoteDescription_media_datachannel_bundled(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        pc1_states = track_states(pc1)
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        """
        initial negotiation
        """

        # create offer
        pc1.addTrack(AudioStreamTrack())
        pc1.createDataChannel("chat", protocol="")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])
        self.assertTrue("a=group:BUNDLE 0 1" in pc1.localDescription.sdp)
        self.assertTrue("m=audio " in pc1.localDescription.sdp)

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("a=group:BUNDLE 0 1" in answer.sdp)
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=application " in answer.sdp)

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)
        self.assertEqual(mids(pc2), ["0", "1"])
        self.assertTrue("a=group:BUNDLE 0 1" in pc2.localDescription.sdp)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=application " in pc2.localDescription.sdp)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        """
        renegotiation
        """

        # create offer
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "completed")
        self.assertEqual(pc1.iceGatheringState, "complete")
        self.assertEqual(mids(pc1), ["0", "1"])
        self.assertTrue("a=group:BUNDLE 0 1" in pc1.localDescription.sdp)
        self.assertTrue("m=audio " in pc1.localDescription.sdp)
        self.assertTrue("m=application " in pc1.localDescription.sdp)
        self.assertHasDtls(pc1.localDescription, "actpass")

        # handle offer
        await pc2.setRemoteDescription(pc1.localDescription)
        self.assertEqual(pc2.remoteDescription, pc1.localDescription)
        self.assertEqual(len(pc2.getReceivers()), 1)
        self.assertEqual(len(pc2.getSenders()), 1)
        self.assertEqual(len(pc2.getTransceivers()), 1)
        self.assertEqual(mids(pc2), ["0", "1"])

        # create answer
        answer = await pc2.createAnswer()
        self.assertEqual(answer.type, "answer")
        self.assertTrue("a=group:BUNDLE 0 1" in answer.sdp)
        self.assertTrue("m=audio " in answer.sdp)
        self.assertTrue("m=application " in answer.sdp)

        await pc2.setLocalDescription(answer)
        self.assertEqual(pc2.iceConnectionState, "completed")
        self.assertEqual(pc2.iceGatheringState, "complete")
        self.assertEqual(mids(pc2), ["0", "1"])
        self.assertTrue("a=group:BUNDLE 0 1" in pc2.localDescription.sdp)
        self.assertTrue("m=audio " in pc2.localDescription.sdp)
        self.assertTrue("m=application " in pc2.localDescription.sdp)
        self.assertHasDtls(pc2.localDescription, "active")

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)
        self.assertEqual(pc1.iceConnectionState, "completed")

        # allow media to flow long enough to collect stats
        await asyncio.sleep(2)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc1_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc1_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc1_states["signalingState"],
            [
                "stable",
                "have-local-offer",
                "stable",
                "have-local-offer",
                "stable",
                "closed",
            ],
        )

        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["iceConnectionState"], ["new", "checking", "completed", "closed"]
        )
        self.assertEqual(
            pc2_states["iceGatheringState"], ["new", "gathering", "complete"]
        )
        self.assertEqual(
            pc2_states["signalingState"],
            [
                "stable",
                "have-remote-offer",
                "stable",
                "have-remote-offer",
                "stable",
                "closed",
            ],
        )

    @asynctest
    async def test_dtls_role_offer_actpass(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        pc1_states = track_states(pc1)
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.createDataChannel("chat", protocol="")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")

        # set remote description
        await pc2.setRemoteDescription(pc1.localDescription)

        # create answer
        answer = await pc2.createAnswer()
        self.assertHasDtls(answer, "active")

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        self.assertEqual(pc1.sctp.transport._role, "server")
        self.assertEqual(pc2.sctp.transport._role, "client")
        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )

    @asynctest
    async def test_dtls_role_offer_passive(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        pc1_states = track_states(pc1)
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.createDataChannel("chat", protocol="")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")

        # handle offer with replaced DTLS role
        await pc2.setRemoteDescription(
            RTCSessionDescription(
                type="offer", sdp=pc1.localDescription.sdp.replace("actpass", "passive")
            )
        )

        # create answer
        answer = await pc2.createAnswer()
        self.assertHasDtls(answer, "active")

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # pc1 is explicity passive so server.
        self.assertEqual(pc1.sctp.transport._role, "server")
        self.assertEqual(pc2.sctp.transport._role, "client")
        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )

    @asynctest
    async def test_dtls_role_offer_active(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        pc1_states = track_states(pc1)
        pc2_states = track_states(pc2)

        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "new")
        self.assertIsNone(pc1.localDescription)
        self.assertIsNone(pc1.remoteDescription)

        self.assertEqual(pc2.iceConnectionState, "new")
        self.assertEqual(pc2.iceGatheringState, "new")
        self.assertIsNone(pc2.localDescription)
        self.assertIsNone(pc2.remoteDescription)

        # create offer
        pc1.createDataChannel("chat", protocol="")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")

        await pc1.setLocalDescription(offer)
        self.assertEqual(pc1.iceConnectionState, "new")
        self.assertEqual(pc1.iceGatheringState, "complete")

        # handle offer with replaced DTLS role
        await pc2.setRemoteDescription(
            RTCSessionDescription(
                type="offer", sdp=pc1.localDescription.sdp.replace("actpass", "active")
            )
        )

        # create answer
        answer = await pc2.createAnswer()
        self.assertHasDtls(answer, "passive")

        await pc2.setLocalDescription(answer)
        await self.assertIceChecking(pc2)

        # handle answer
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc1.remoteDescription, pc2.localDescription)

        # check outcome
        await self.assertIceCompleted(pc1, pc2)

        # pc1 is explicity active so client.
        self.assertEqual(pc1.sctp.transport._role, "client")
        self.assertEqual(pc2.sctp.transport._role, "server")
        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # check state changes
        self.assertEqual(
            pc1_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )
        self.assertEqual(
            pc2_states["connectionState"], ["new", "connecting", "connected", "closed"]
        )

    @asynctest
    async def test_right_mid_order(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        tr1_a = pc1.addTransceiver("video", "recvonly")
        tr1_b = pc1.addTransceiver("video", "recvonly")
        offer = await pc1.createOffer()
        self.assertEqual(offer.type, "offer")

        await pc1.setLocalDescription(offer)

        tr2_a = pc2.addTransceiver(VideoStreamTrack())
        tr2_b = pc2.addTransceiver(VideoStreamTrack())
        await pc2.setRemoteDescription(offer)

        self.assertEqual(tr1_a.mid, tr2_a.mid)
        self.assertEqual(tr1_b.mid, tr2_b.mid)

        # close
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

    @asynctest
    async def test_bundlepolicy_max_bundle_ufrag_and_pwd(self) -> None:
        pc = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)
        )
        transceiver1 = pc.addTransceiver("audio")
        transceiver2 = pc.addTransceiver("video")

        await pc.createOffer()
        param1 = (
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters()
        )
        param2 = (
            transceiver2.receiver.transport.transport.iceGatherer.getLocalParameters()
        )
        self.assertEqual(param1.usernameFragment, param2.usernameFragment)
        self.assertEqual(param1.password, param2.password)
        self.assertEqual(
            transceiver1.receiver.transport, transceiver2.receiver.transport
        )

    @asynctest
    async def test_bundlepolicy_max_bundle_ufrag_and_pwd_datachannel(self) -> None:
        pc = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)
        )
        pc.createDataChannel("somechannel")
        transceiver = pc.addTransceiver("audio")

        await pc.createOffer()
        param1 = (
            transceiver.receiver.transport.transport.iceGatherer.getLocalParameters()
        )
        param2 = pc.sctp.transport.transport.iceGatherer.getLocalParameters()
        self.assertEqual(param1.usernameFragment, param2.usernameFragment)
        self.assertEqual(param1.password, param2.password)
        self.assertEqual(transceiver.receiver.transport, pc.sctp.transport)

        self.assertEqual(
            transceiver.receiver.transport.transport.iceGatherer.getLocalParameters(),
            pc.sctp.transport.transport.iceGatherer.getLocalParameters(),
        )

    @asynctest
    async def test_bundlepolicy_transports_balanced(self) -> None:
        pc = RTCPeerConnection(RTCConfiguration(bundlePolicy=RTCBundlePolicy.BALANCED))
        transceiver1 = pc.addTransceiver("audio")
        transceiver2 = pc.addTransceiver("video")
        transceiver3 = pc.addTransceiver("audio")
        pc.createDataChannel("somechannel")
        self.assertNotEqual(
            transceiver1.receiver.transport, transceiver2.receiver.transport
        )
        self.assertEqual(
            transceiver1.receiver.transport, transceiver3.receiver.transport
        )
        self.assertNotEqual(transceiver1.receiver.transport, pc.sctp.transport)
        self.assertNotEqual(transceiver2.receiver.transport, pc.sctp.transport)

        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            transceiver2.receiver.transport.transport.iceGatherer.getLocalParameters(),
        )
        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            transceiver3.receiver.transport.transport.iceGatherer.getLocalParameters(),
        )
        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            pc.sctp.transport.transport.iceGatherer.getLocalParameters(),
        )

    @asynctest
    async def test_bundlepolicy_transports_max_compat(self) -> None:
        pc = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_COMPAT)
        )
        transceiver1 = pc.addTransceiver("audio")
        transceiver2 = pc.addTransceiver("video")
        transceiver3 = pc.addTransceiver("audio")
        pc.createDataChannel("somechannel")
        self.assertNotEqual(
            transceiver1.receiver.transport, transceiver2.receiver.transport
        )
        self.assertNotEqual(
            transceiver1.receiver.transport, transceiver3.receiver.transport
        )
        self.assertNotEqual(transceiver1.receiver.transport, pc.sctp.transport)
        self.assertNotEqual(transceiver2.receiver.transport, pc.sctp.transport)

        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            transceiver2.receiver.transport.transport.iceGatherer.getLocalParameters(),
        )
        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            transceiver3.receiver.transport.transport.iceGatherer.getLocalParameters(),
        )
        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            pc.sctp.transport.transport.iceGatherer.getLocalParameters(),
        )

    @asynctest
    async def test_bundlepolicy_transports_max_bundle(self) -> None:
        pc = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_BUNDLE)
        )
        transceiver1 = pc.addTransceiver("audio")
        transceiver2 = pc.addTransceiver("video")
        transceiver3 = pc.addTransceiver("audio")
        pc.createDataChannel("somechannel")
        self.assertEqual(
            transceiver1.receiver.transport, transceiver2.receiver.transport
        )
        self.assertEqual(
            transceiver1.receiver.transport, transceiver3.receiver.transport
        )
        self.assertEqual(transceiver1.receiver.transport, pc.sctp.transport)

        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            transceiver2.receiver.transport.transport.iceGatherer.getLocalParameters(),
        )
        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            transceiver3.receiver.transport.transport.iceGatherer.getLocalParameters(),
        )
        self.assertEqual(
            transceiver1.receiver.transport.transport.iceGatherer.getLocalParameters(),
            pc.sctp.transport.transport.iceGatherer.getLocalParameters(),
        )

    @asynctest
    async def test_always_negotiate_datachannels(self) -> None:
        pc = RTCPeerConnection(RTCConfiguration(alwaysNegotiateDataChannels=True))
        pc.addTransceiver("audio")
        offer = await pc.createOffer()
        parsed = SessionDescription.parse(offer.sdp)
        self.assertEqual(len(parsed.media), 2)
        self.assertEqual("application", parsed.media[0].kind)
        self.assertEqual("audio", parsed.media[1].kind)

    @asynctest
    async def test_always_negotiate_datachannels_subsequent(self) -> None:
        pc1 = RTCPeerConnection(RTCConfiguration(alwaysNegotiateDataChannels=True))
        pc2 = RTCPeerConnection()

        pc1.addTransceiver("audio")
        await pc1.setLocalDescription()
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription()
        await pc1.setRemoteDescription(pc2.localDescription)

        for pc in [pc1, pc2]:
            parsed = SessionDescription.parse(pc.localDescription.sdp)
            self.assertEqual(len(parsed.media), 2)

            pc.createDataChannel("test")
            offer = await pc.createOffer()
            parsed = SessionDescription.parse(offer.sdp)
            self.assertEqual(len(parsed.media), 2)
