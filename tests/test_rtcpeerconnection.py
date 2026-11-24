import asyncio
import gc
import re
from collections.abc import Callable
from typing import Any, Optional, Union
from unittest import TestCase
from unittest.mock import patch

import aioice.stun
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCDataChannel,
    RTCIceCandidate,
    RTCIceTransportPolicy,
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
from aiortc.mediastreams import (
    AudioStreamTrack,
    MediaStreamError,
    MediaStreamTrack,
    VideoStreamTrack,
)
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
from aiortc.rtcrtpreceiver import RTCRtpReceiver
from aiortc.rtcrtpsender import RTCRtpSender
from aiortc.sdp import ExtmapDescription, SessionDescription
from aiortc.stats import (
    RTCInboundRtpStreamStats,
    RTCOutboundRtpStreamStats,
    RTCStatsReport,
)
from av import AudioFrame

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

def candidate_to_sdp_string(candidate) -> str:
    """Convert an RTCIceCandidate to a browser-format candidate string."""
    s = f"candidate:{candidate.foundation} {candidate.component} {candidate.protocol.upper()} {candidate.priority} {candidate.ip} {candidate.port} typ {candidate.type}"
    if candidate.relatedAddress:
        s += f" raddr {candidate.relatedAddress}"
    if candidate.relatedPort:
        s += f" rport {candidate.relatedPort}"
    if candidate.tcpType:
        s += f" tcptype {candidate.tcpType}"
    return s

def audio_codecs(*names: str) -> list[RTCRtpCodecCapability]:
    codecs = RTCRtpSender.getCapabilities("audio").codecs
    return [codec for name in names for codec in codecs if codec.name == name]


def bundle_groups(description: RTCSessionDescription) -> list[list[Union[int, str]]]:
    parsed = SessionDescription.parse(description.sdp)
    return [group.items for group in parsed.group if group.semantic == "BUNDLE"]


def media_ports(description: RTCSessionDescription) -> list[int]:
    parsed = SessionDescription.parse(description.sdp)
    return [media.port for media in parsed.media]


def mids(pc: RTCPeerConnection) -> list[Optional[str]]:
    mids = [x.mid for x in pc.getTransceivers()]
    if pc.sctp:
        mids.append(pc.sctp.mid)
    return sorted(mids)


async def negotiate(pc1: RTCPeerConnection, pc2: RTCPeerConnection) -> None:
    await pc1.setLocalDescription(await pc1.createOffer())
    await pc2.setRemoteDescription(pc1.localDescription)
    await pc2.setLocalDescription(await pc2.createAnswer())
    await pc1.setRemoteDescription(pc2.localDescription)


async def outbound_ssrc(sender: RTCRtpSender) -> int:
    report = await sender.getStats()
    return next(
        stats.ssrc
        for stats in report.values()
        if isinstance(stats, RTCOutboundRtpStreamStats)
    )


async def packets_received(receiver: RTCRtpReceiver) -> int:
    report = await receiver.getStats()
    return sum(
        stats.packetsReceived
        for stats in report.values()
        if isinstance(stats, RTCInboundRtpStreamStats)
    )


def strip_ice_candidates(description: RTCSessionDescription) -> RTCSessionDescription:
    return RTCSessionDescription(
        sdp=STRIP_CANDIDATES_RE.sub("", description.sdp), type=description.type
    )


def strip_ice_candidates_from_section(
    description: RTCSessionDescription, index: int
) -> RTCSessionDescription:
    sections = description.sdp.split("m=")
    sections[index + 1] = STRIP_CANDIDATES_RE.sub("", sections[index + 1])
    return RTCSessionDescription(sdp="m=".join(sections), type=description.type)


async def wait_for_ended(track: MediaStreamTrack) -> None:
    try:
        while True:
            await track.recv()
    except MediaStreamError:
        pass


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

    def test_find_common_codecs_dynamic(self) -> None:
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

    def test_find_common_codecs_feedback(self) -> None:
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

    def test_find_common_codecs_rtx_listed_first(self) -> None:
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
                mimeType="video/rtx",
                clockRate=90000,
                payloadType=97,
                parameters={"apt": 96},
            ),
            RTCRtpCodecParameters(
                mimeType="video/VP8", clockRate=90000, payloadType=96
            ),
        ]
        common = find_common_codecs(local_codecs, remote_codecs)
        self.assertEqual(
            common,
            [
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=97,
                    parameters={"apt": 96},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/VP8", clockRate=90000, payloadType=96
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

    def assertBundleTagInactive(self, description: RTCSessionDescription) -> None:
        """
        Check the BUNDLE-tagged "m=" section is kept bundled, as inactive.
        """
        parsed = SessionDescription.parse(description.sdp)
        self.assertNotEqual(parsed.media[0].port, 0)
        self.assertEqual(parsed.media[0].direction, "inactive")
        self.assertEqual(
            bundle_groups(description), [[m.rtp.muxId for m in parsed.media]]
        )

    def assertClosed(self, pc: RTCPeerConnection) -> None:
        self.assertEqual(pc.connectionState, "closed")
        self.assertEqual(pc.iceConnectionState, "closed")
        self.assertEqual(pc.signalingState, "closed")

    def assertCodecNames(
        self, description: RTCSessionDescription, names: list[str]
    ) -> None:
        parsed = SessionDescription.parse(description.sdp)
        self.assertEqual(
            [codec.name for media in parsed.media for codec in media.rtp.codecs],
            names,
        )

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
            lambda: (
                pc1.iceConnectionState == "checking"
                or pc2.iceConnectionState == "checking"
            )
        )
        self.assertEqual(pc1.iceConnectionState, "completed")
        self.assertEqual(pc2.iceConnectionState, "completed")

    async def assertIceNew(self, pc: RTCPeerConnection) -> None:
        # Give the connection attempt a chance to run.
        await asyncio.sleep(0.1)
        self.assertEqual(pc.iceConnectionState, "new")
        self.assertEqual(pc.iceGatheringState, "complete")

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
    async def test_addIceCandidate_browser_string(self) -> None:
        pc = RTCPeerConnection()
        pc.createDataChannel("test")
        offer = await pc.createOffer()
        await pc.setRemoteDescription(offer)
        self.assertFalse("a=candidate:" in pc.remoteDescription.sdp)

        candidate_with_index = RTCIceCandidate(
            candidate="candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ host",
            sdpMLineIndex=0,
        )
        await pc.addIceCandidate(candidate_with_index)
        self.assertTrue("a=candidate:" in pc.remoteDescription.sdp)

        candidate_with_mid = RTCIceCandidate(
            candidate="candidate:0 1 UDP 2122252543 192.168.99.7 33544 typ host",
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
    async def test_addIceCandidate_browser_string_no_sdpMid_or_sdpMLineIndex(self) -> None:
        pc = RTCPeerConnection()
        with self.assertRaises(ValueError) as cm:
            await pc.addIceCandidate(
                RTCIceCandidate(
                    candidate="candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ host",
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

        await pc.close()

    @asynctest
    async def test_addIceCandidate_browser_string_before_setremotedescription(self) -> None:
        pc = RTCPeerConnection()
        pc.createDataChannel("test")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        candidate_with_index = RTCIceCandidate(
            candidate="candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ host",
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
    async def test_addTrack_stopped_transceiver(self) -> None:
        pc = RTCPeerConnection()

        # A stopped transceiver is not reused, even if it has no track.
        stopped = pc.addTransceiver("audio")
        await stopped.stop()
        track = AudioStreamTrack()
        sender = pc.addTrack(track)
        self.assertIsNone(stopped.sender.track)
        self.assertEqual(sender.track, track)
        self.assertEqual(len(pc.getTransceivers()), 2)

        await pc.close()

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

    @asynctest
    async def test_close_before_connect(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())

        await pc1.setLocalDescription()
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription()

        # Close before the connection task scheduled above gets to run.
        with self.assertNoLogs("asyncio", level="ERROR"):
            await pc2.close()
            gc.collect()

        await pc1.close()

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
        await self.assertIceNew(pc2)
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

    async def _test_connect_audio_bidirectional_trickle_browser_string(self, with_mid: bool) -> None:
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

        # trickle candidates in browser string format
        for transceiver in pc2.getTransceivers():
            iceGatherer = transceiver.sender.transport.transport.iceGatherer
            for candidate in iceGatherer.getLocalCandidates():
                print(type(candidate))
                print(dir(candidate))
                await pc1.addIceCandidate(RTCIceCandidate(
                    candidate="candidate:" + candidate_to_sdp_string(candidate),
                    sdpMid=transceiver.mid if with_mid else None,
                    sdpMLineIndex=None if with_mid else transceiver._get_mline_index(),
                ))
        for transceiver in pc1.getTransceivers():
            iceGatherer = transceiver.sender.transport.transport.iceGatherer
            for candidate in iceGatherer.getLocalCandidates():
                await pc2.addIceCandidate(RTCIceCandidate(
                    candidate="candidate:" + candidate_to_sdp_string(candidate),
                    sdpMid=transceiver.mid if with_mid else None,
                    sdpMLineIndex=None if with_mid else transceiver._get_mline_index(),
                ))

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
    async def test_connect_audio_bidirectional_trickle_browser_string_with_mid(self) -> None:
        await self._test_connect_audio_bidirectional_trickle_browser_string(with_mid=True)

    @asynctest
    async def test_connect_audio_bidirectional_trickle_browser_string_with_mline_index(self) -> None:
        await self._test_connect_audio_bidirectional_trickle_browser_string(with_mid=False)

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
        self.assertCodecNames(pc1.localDescription, ["PCMA", "PCMU"])
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
        self.assertCodecNames(pc2.localDescription, ["PCMA", "PCMU"])
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
    async def test_connect_audio_codec_removed_new_clock_rate(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(AudioStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        async def recv_sample_rate(track: MediaStreamTrack) -> int:
            frame = await track.recv()
            assert isinstance(frame, AudioFrame)
            return frame.sample_rate

        # pc1 sends Opus.
        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)
        for track in pc2_tracks:
            self.assertEqual(await recv_sample_rate(track), 48000)
        ssrcs = [await outbound_ssrc(sender) for sender in pc1.getSenders()]

        # The answer removes Opus, so pc1 switches to PCMU. The SSRCs are kept,
        # as pc2 could not tell the tracks apart by payload type.
        for transceiver in pc2.getTransceivers():
            transceiver.setCodecPreferences(audio_codecs("PCMU"))
        await negotiate(pc1, pc2)
        self.assertCodecNames(pc2.localDescription, ["PCMU", "PCMU"])

        async def wait_for_pcmu(track: MediaStreamTrack) -> None:
            while await recv_sample_rate(track) != 8000:
                pass

        for track in pc2_tracks:
            await asyncio.wait_for(wait_for_pcmu(track), 2)
        self.assertEqual(
            [await outbound_ssrc(sender) for sender in pc1.getSenders()], ssrcs
        )

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_codec_removed_same_clock_rate(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.getTransceivers()[0].setCodecPreferences(audio_codecs("PCMU", "PCMA"))
        sender = pc1.getSenders()[0]

        pc2 = RTCPeerConnection()

        # pc1 sends PCMU.
        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)
        receiver = pc2.getReceivers()[0]
        await self.sleepWhile(lambda: not receiver.getSynchronizationSources())
        ssrc = await outbound_ssrc(sender)

        # The answer removes PCMU, so pc1 switches to PCMA, whose clock rate
        # is the same, so the SSRC is kept (RFC 9429 section 5.11).
        pc2.getTransceivers()[0].setCodecPreferences(audio_codecs("PCMA"))
        await negotiate(pc1, pc2)
        self.assertCodecNames(pc2.localDescription, ["PCMA"])

        await asyncio.sleep(0.5)
        count = await packets_received(receiver)
        await asyncio.sleep(0.5)
        self.assertGreater(await packets_received(receiver), count)
        self.assertEqual(await outbound_ssrc(sender), ssrc)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_codec_unchanged_by_offer(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())

        # The answer only accepts PCMU.
        pc2 = RTCPeerConnection()
        pc2.addTransceiver("audio", direction="recvonly")
        pc2.getTransceivers()[0].setCodecPreferences(audio_codecs("PCMU"))

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)
        self.assertCodecNames(pc2.localDescription, ["PCMU"])

        receiver = pc2.getReceivers()[0]
        await self.sleepWhile(lambda: not receiver.getSynchronizationSources())

        # A new offer lists Opus again, but pc1 keeps sending PCMU until it
        # applies the answer (RFC 9429 section 5.11).
        await pc1.setLocalDescription(await pc1.createOffer())

        await asyncio.sleep(0.5)
        count = await packets_received(receiver)
        await asyncio.sleep(0.5)
        self.assertGreater(await packets_received(receiver), count)

        await pc1.close()
        await pc2.close()

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
    async def test_connect_audio_stopped_by_answerer(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_tracks = track_remote_tracks(pc1)
        pc1.addTrack(AudioStreamTrack())

        pc2 = RTCPeerConnection()
        pc2.on("track", lambda track: pc2.addTrack(AudioStreamTrack()))

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # The answer rejects the "m=" section of the stopped transceiver
        # (RFC 9429 section 5.3.1).
        await pc2.getTransceivers()[0].stop()
        await negotiate(pc1, pc2)
        self.assertEqual(media_ports(pc2.localDescription), [0])
        self.assertEqual(bundle_groups(pc2.localDescription), [])

        # Applying the answer stops the offerer's transceiver, which ends its
        # remote track (W3C webrtc-pc, set the session description).
        self.assertTrue(pc1.getTransceivers()[0].stopped)
        self.assertEqual(pc1.getTransceivers()[0].currentDirection, "inactive")
        await asyncio.wait_for(wait_for_ended(pc1_tracks[0]), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_stopped_by_offerer(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # The offer rejects the "m=" section of the stopped transceiver, without
        # its "a=msid" line (RFC 9429 section 5.2.2), and so does the answer.
        await pc1.getTransceivers()[0].stop()
        await pc1.setLocalDescription(await pc1.createOffer())
        self.assertEqual(media_ports(pc1.localDescription), [0])
        self.assertEqual(bundle_groups(pc1.localDescription), [])
        self.assertNotIn("a=msid:", pc1.localDescription.sdp)

        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(media_ports(pc2.localDescription), [0])
        self.assertEqual(bundle_groups(pc2.localDescription), [])

        # Applying the offer stops the answerer's transceiver, which ends its
        # remote track (W3C webrtc-pc, set the session description).
        self.assertTrue(pc2.getTransceivers()[0].stopped)
        self.assertEqual(pc2.getTransceivers()[0].currentDirection, "inactive")
        await asyncio.wait_for(wait_for_ended(pc2_tracks[0]), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_two_tracks_first_rejected(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(AudioStreamTrack())

        # pc2 has a transceiver which is not associated yet.
        pc2 = RTCPeerConnection()
        pc2.addTrack(AudioStreamTrack())
        pc2_tracks = track_remote_tracks(pc2)
        transceiver = pc2.getTransceivers()[0]

        # The first "m=" section is rejected, as an SFU may offer for a
        # participant who left.
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        rejected_mid, accepted_mid = [m.rtp.muxId for m in parsed.media]
        parsed.media[0].port = 0
        parsed.group[0].items = [accepted_mid]
        await pc2.setRemoteDescription(
            RTCSessionDescription(sdp=str(parsed), type="offer")
        )
        await pc2.setLocalDescription(await pc2.createAnswer())
        self.assertEqual(media_ports(pc2.localDescription)[0], 0)
        self.assertEqual(bundle_groups(pc2.localDescription), [[accepted_mid]])

        # pc2's transceiver is used for the accepted "m=" section, and a new
        # stopped one for the rejected "m=" section.
        self.assertEqual(transceiver.mid, accepted_mid)
        self.assertFalse(transceiver.stopped)
        self.assertEqual(
            [(t.mid, t.stopped, t.currentDirection) for t in pc2.getTransceivers()],
            [(accepted_mid, False, "sendrecv"), (rejected_mid, True, "inactive")],
        )

        # The connection is established, and the accepted "m=" section's media
        # flows.
        await pc1.setRemoteDescription(pc2.localDescription)
        await self.assertIceCompleted(pc1, pc2)
        await self.sleepWhile(
            lambda: "connecting" in [pc1.connectionState, pc2.connectionState]
        )
        self.assertEqual(pc1.connectionState, "connected")
        self.assertEqual(pc2.connectionState, "connected")
        self.assertEqual([t.kind for t in pc2_tracks], ["audio"])
        await asyncio.wait_for(pc2_tracks[0].recv(), 1)

        await pc1.close()
        await pc2.close()

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
    async def test_connect_audio_two_tracks_ssrc_changes(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(AudioStreamTrack())

        pc2 = RTCPeerConnection()

        def receiving() -> list[bool]:
            return [bool(r.getSynchronizationSources()) for r in pc2.getReceivers()]

        # Advertise SSRCs which pc1 does not send, as an SFU does before it
        # reuses the "m=" sections for new participants.
        await pc1.setLocalDescription(await pc1.createOffer())
        mangled = RTCSessionDescription(
            sdp=re.sub(
                "^a=ssrc:([0-9]+)",
                lambda m: f"a=ssrc:{int(m.group(1)) ^ 1}",
                pc1.localDescription.sdp,
                flags=re.M,
            ),
            type=pc1.localDescription.type,
        )
        await pc2.setRemoteDescription(mangled)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)
        await self.assertIceCompleted(pc1, pc2)

        # The tracks share payload types, so their RTP cannot be routed.
        await asyncio.sleep(0.5)
        self.assertEqual(receiving(), [False, False])

        # Renegotiate with the SSRCs which pc1 actually sends, which must not
        # restart a stopped receiver.
        await pc2.getTransceivers()[0].stop()
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)

        await asyncio.sleep(0.5)
        self.assertEqual(receiving(), [False, True])

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_without_ssrc_then_second_track(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())

        pc2 = RTCPeerConnection()

        async def negotiate() -> None:
            # Strip out the SSRCs, so pc2 learns them from the payload types.
            await pc1.setLocalDescription(await pc1.createOffer())
            mangled = RTCSessionDescription(
                sdp=re.sub("^a=ssrc.*\r\n", "", pc1.localDescription.sdp, flags=re.M),
                type=pc1.localDescription.type,
            )
            await pc2.setRemoteDescription(mangled)
            await pc2.setLocalDescription(await pc2.createAnswer())
            await pc1.setRemoteDescription(pc2.localDescription)

        await negotiate()
        await self.assertIceCompleted(pc1, pc2)

        receiver = pc2.getReceivers()[0]
        await self.sleepWhile(lambda: not receiver.getSynchronizationSources())
        self.assertEqual(len(receiver.getSynchronizationSources()), 1)

        # The second track shares the payload types of the first one, whose
        # learned SSRC must survive the renegotiation.
        pc1.addTrack(AudioStreamTrack())
        await negotiate()

        count = await packets_received(receiver)
        await asyncio.sleep(0.5)
        self.assertGreater(await packets_received(receiver), count)

        await pc1.close()
        await pc2.close()

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
    async def test_connect_audio_and_video_video_rejected_first_offer(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        # The first offer rejects the video "m=" section, as an SFU may.
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[1].port = 0
        parsed.group[0].items = [parsed.media[0].rtp.muxId]
        await pc2.setRemoteDescription(
            RTCSessionDescription(sdp=str(parsed), type="offer")
        )
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(media_ports(pc2.localDescription)[1], 0)

        # The transports of the video "m=" sections are discarded, so both
        # connections complete (RFC 9429 section 5.11).
        await self.assertIceCompleted(pc1, pc2)
        await self.sleepWhile(
            lambda: "connecting" in [pc1.connectionState, pc2.connectionState]
        )
        self.assertEqual(pc1.connectionState, "connected")
        self.assertEqual(pc2.connectionState, "connected")
        self.assertEqual([t.kind for t in pc2_tracks], ["audio"])
        await asyncio.wait_for(pc2_tracks[0].recv(), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_video_rejected_minimal(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # Reject the video "m=" section as Firefox does, without transport
        # attributes and with a format which has no "a=rtpmap" line, which
        # RFC 3264 section 8.2 allows.
        await pc1.getTransceivers()[1].stop()
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        video = SessionDescription.parse(
            "v=0\r\n"
            "o=- 0 0 IN IP4 0.0.0.0\r\n"
            "s=-\r\n"
            "t=0 0\r\n"
            "m=video 0 UDP/TLS/RTP/SAVPF 120\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            "a=inactive\r\n"
            f"a=mid:{parsed.media[1].rtp.muxId}\r\n"
        ).media[0]
        parsed.media[1] = video
        await pc2.setRemoteDescription(
            RTCSessionDescription(sdp=str(parsed), type="offer")
        )
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(media_ports(pc2.localDescription)[1], 0)
        self.assertNotIn("a=extmap", pc2.localDescription.sdp.split("m=video")[1])
        self.assertEqual(
            bundle_groups(pc2.localDescription), [[parsed.media[0].rtp.muxId]]
        )

        # The video track ends, the audio keeps flowing.
        await asyncio.wait_for(wait_for_ended(pc2_tracks[1]), 1)
        await asyncio.wait_for(pc2_tracks[0].recv(), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_bundle_only(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        # The video "m=" section is bundle-only, so its zero port does not
        # reject it (RFC 9143 section 6).
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[1].port = 0
        parsed.media[1].bundle_only = True
        parsed.media[1].ice_candidates = []
        await pc2.setRemoteDescription(
            RTCSessionDescription(sdp=str(parsed), type="offer")
        )
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertFalse(pc2.getTransceivers()[1].stopped)

        # The video flows over the bundled transport.
        await self.assertIceCompleted(pc1, pc2)
        await asyncio.wait_for(pc2_tracks[1].recv(), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_bundle_tag_rejected(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # An offer which rejects the BUNDLE-tagged audio "m=" section, moving the
        # group to the video one, is refused.
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[0].port = 0
        parsed.group[0].items = [parsed.media[1].rtp.muxId]
        with self.assertRaises(OperationError) as cm:
            await pc2.setRemoteDescription(
                RTCSessionDescription(sdp=str(parsed), type="offer")
            )
        self.assertEqual(
            str(cm.exception),
            "Rejecting the BUNDLE-tagged media section is not supported",
        )
        self.assertEqual(pc2.signalingState, "stable")
        self.assertFalse(pc2.getTransceivers()[0].stopped)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_bundle_tag_stopped_by_answerer(
        self,
    ) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())
        pc1_tracks = track_remote_tracks(pc1)

        # pc2 sends video back.
        pc2 = RTCPeerConnection()
        pc2.addTrack(VideoStreamTrack())

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # The answer keeps the BUNDLE-tagged audio "m=" section of the stopped
        # transceiver bundled, as inactive.
        audio = next(t for t in pc2.getTransceivers() if t.kind == "audio")
        await audio.stop()
        await negotiate(pc1, pc2)
        self.assertBundleTagInactive(pc2.localDescription)
        self.assertEqual(audio.currentDirection, "inactive")

        # The video keeps flowing.
        self.assertFalse(pc1.getTransceivers()[0].stopped)
        await asyncio.wait_for(pc1_tracks[0].recv(), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_bundle_tag_stopped_by_offerer(
        self,
    ) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # The offer keeps the BUNDLE-tagged audio "m=" section of the stopped
        # transceiver bundled, as inactive.
        await pc1.getTransceivers()[0].stop()
        await negotiate(pc1, pc2)
        self.assertBundleTagInactive(pc1.localDescription)

        # The video keeps flowing.
        self.assertFalse(pc2.getTransceivers()[0].stopped)
        await asyncio.wait_for(pc2_tracks[1].recv(), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_no_common_audio(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        # The first offer's audio "m=" section, which is BUNDLE-tagged, has no
        # supported format, so the answer rejects it and the BUNDLE group
        # moves to the video one (RFC 9143 section 7.3.1).
        await pc1.setLocalDescription(await pc1.createOffer())
        mangled = RTCSessionDescription(
            sdp=re.sub(
                "^a=rtpmap:[0-9]+ (opus|G722|PCMU|PCMA)/.*\r\n",
                "",
                pc1.localDescription.sdp,
                flags=re.M,
            ),
            type="offer",
        )
        await pc2.setRemoteDescription(mangled)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)
        parsed = SessionDescription.parse(pc2.localDescription.sdp)
        self.assertEqual(media_ports(pc2.localDescription)[0], 0)
        self.assertEqual(
            bundle_groups(pc2.localDescription), [[parsed.media[1].rtp.muxId]]
        )

        # The video flows.
        await self.assertIceCompleted(pc1, pc2)
        self.assertEqual([t.kind for t in pc2_tracks], ["video"])
        await asyncio.wait_for(pc2_tracks[0].recv(), 1)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_offer_without_bundle(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        async def answer_without_bundle() -> None:
            await pc1.setLocalDescription(await pc1.createOffer())
            parsed = SessionDescription.parse(pc1.localDescription.sdp)
            parsed.group = []
            await pc2.setRemoteDescription(
                RTCSessionDescription(sdp=str(parsed), type="offer")
            )
            await pc2.setLocalDescription(await pc2.createAnswer())

        # An offer without a BUNDLE group gets an answer without one
        # (RFC 9429 section 5.3.1).
        await answer_without_bundle()
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(bundle_groups(pc2.localDescription), [])

        # The media flows over separate transports.
        await self.assertIceCompleted(pc1, pc2)
        self.assertEqual([t.kind for t in pc2_tracks], ["audio", "video"])
        for track in pc2_tracks:
            await asyncio.wait_for(track.recv(), 1)

        # Applying an answer which rejects the video "m=" section discards its
        # transport (RFC 9429 section 5.11).
        video = pc2.getTransceivers()[1]
        await video.stop()
        await answer_without_bundle()
        self.assertEqual(media_ports(pc2.localDescription)[1], 0)
        self.assertEqual(video.receiver.transport.state, "closed")
        await pc1.setRemoteDescription(pc2.localDescription)
        self.assertEqual(pc2.connectionState, "connected")
        await asyncio.wait_for(pc2_tracks[0].recv(), 1)

        # A new video transceiver gets a new transport, not the discarded one.
        pc1.addTransceiver(VideoStreamTrack())
        await answer_without_bundle()
        await pc1.setRemoteDescription(pc2.localDescription)
        await self.sleepWhile(lambda: len(pc2_tracks) < 3)
        await asyncio.wait_for(pc2_tracks[2].recv(), 2)

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_connect_audio_and_video_stopped_by_offerer(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc1.addTrack(VideoStreamTrack())

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # With no other "m=" section left, the BUNDLE-tagged one is rejected too.
        for transceiver in pc1.getTransceivers():
            await transceiver.stop()
        await negotiate(pc1, pc2)
        self.assertEqual(media_ports(pc1.localDescription), [0, 0])
        self.assertEqual(bundle_groups(pc1.localDescription), [])

        for track in pc2_tracks:
            await asyncio.wait_for(wait_for_ended(track), 1)

        await pc1.close()
        await pc2.close()

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
    async def test_connect_audio_then_video_no_task_error(self) -> None:
        # Exceptions in background tasks are only reported when the task is
        # collected, to the loop's exception handler.
        errors: list[dict[str, Any]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context)
        )

        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())

        pc2 = RTCPeerConnection()

        await negotiate(pc1, pc2)
        await self.assertIceCompleted(pc1, pc2)

        # Connecting the transceivers must not run while pc2 applies its answer,
        # whose new "m=" section is then missing from its local description.
        pc1.addTrack(VideoStreamTrack())
        await negotiate(pc1, pc2)
        await asyncio.sleep(0.5)
        gc.collect()
        self.assertEqual(errors, [])

        await pc1.close()
        await pc2.close()

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

        # Strip out VP8 along with its RTX codec.
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        del parsed.media[0].rtp.codecs[0:2]
        del parsed.media[0].fmt[0:2]
        desc1 = RTCSessionDescription(sdp=str(parsed), type=pc1.localDescription.type)
        self.assertCodecNames(desc1, ["H264", "rtx", "H264", "rtx"])

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
        self.assertCodecNames(pc2.localDescription, ["H264", "rtx", "H264", "rtx"])

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
    async def test_connect_video_h264_answer_with_other_payload_types(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_tracks = track_remote_tracks(pc1)

        pc2 = RTCPeerConnection()

        # Offer to receive video with the default codecs.
        pc1.addTransceiver("video", direction="recvonly")
        await pc1.setLocalDescription(await pc1.createOffer())
        self.assertCodecNames(
            pc1.localDescription, ["VP8", "rtx", "H264", "rtx", "H264", "rtx"]
        )

        # Accept only H264 constrained baseline, which the offer gives PT 101.
        pc2.addTrack(VideoStreamTrack())
        capabilities = RTCRtpSender.getCapabilities("video")
        preferences = [
            c
            for c in capabilities.codecs
            if c.name == "rtx" or c.parameters.get("profile-level-id") == "42e01f"
        ]
        pc2.getTransceivers()[0].setCodecPreferences(preferences)
        await pc2.setRemoteDescription(pc1.localDescription)

        # Create the answer.
        await pc2.setLocalDescription(await pc2.createAnswer())
        await self.assertIceChecking(pc2)
        self.assertCodecNames(pc2.localDescription, ["H264", "rtx"])

        # Relabel the answer with the PTs of the other H264 profile, listing
        # each twice as in issue #1486. The answerer still sends with the PTs
        # of the offer (RFC 3264 section 6.1).
        parsed = SessionDescription.parse(pc2.localDescription.sdp)
        media = parsed.media[0]
        self.assertEqual(media.fmt, [101, 102])
        media.rtp.codecs[0].payloadType = 99
        media.rtp.codecs[1].payloadType = 100
        media.rtp.codecs[1].parameters["apt"] = 99
        media.rtp.codecs = media.rtp.codecs * 2
        media.fmt = [99, 100, 99, 100]
        answer = RTCSessionDescription(sdp=str(parsed), type="answer")

        # Record the receive parameters while applying the answer.
        with patch.object(
            RTCRtpReceiver,
            "receive",
            autospec=True,
            side_effect=RTCRtpReceiver.receive,
        ) as receive:
            await pc1.setRemoteDescription(answer)
            await self.assertIceCompleted(pc1, pc2)

            # Check that pc1 receives video.
            self.assertEqual(len(pc1_tracks), 1)
            frame = await asyncio.wait_for(pc1_tracks[0].recv(), timeout=5)
            self.assertIsNotNone(frame)

        # Check that pc1 receives H264 and RTX with the PTs of its offer.
        self.assertEqual(receive.call_count, 1)
        parameters = receive.call_args.args[1]
        self.assertEqual([c.payloadType for c in parameters.codecs], [101, 102])
        self.assertEqual([e.payloadType for e in parameters.encodings], [101])
        self.assertIsNotNone(parameters.encodings[0].rtx)

        # Close the connections.
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

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
    async def test_connect_video_rtpmap_out_of_order(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # Create an offer whose rtpmap lines list each RTX codec first.
        pc1.addTrack(VideoStreamTrack())
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        codecs = parsed.media[0].rtp.codecs
        parsed.media[0].rtp.codecs = [codecs[i] for i in (1, 0, 3, 2, 5, 4)]
        offer = RTCSessionDescription(sdp=str(parsed), type="offer")

        # Check the answer keeps every RTX codec, in the order of the offer.
        await pc2.setRemoteDescription(offer)
        pc2.addTrack(VideoStreamTrack())
        await pc2.setLocalDescription(await pc2.createAnswer())
        self.assertCodecNames(
            pc2.localDescription, ["VP8", "rtx", "H264", "rtx", "H264", "rtx"]
        )
        answer = SessionDescription.parse(pc2.localDescription.sdp)
        self.assertEqual(answer.media[0].fmt, parsed.media[0].fmt)

        # Close the connections.
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

    @asynctest
    async def test_connect_video_rtx_listed_first(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_tracks = track_remote_tracks(pc1)

        pc2 = RTCPeerConnection()
        pc2_tracks = track_remote_tracks(pc2)

        # Create an offer whose "m=" line lists each RTX codec first.
        pc1.addTrack(VideoStreamTrack())
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        media = parsed.media[0]
        media.rtp.codecs = [media.rtp.codecs[i] for i in (1, 0, 3, 2, 5, 4)]
        media.fmt = [codec.payloadType for codec in media.rtp.codecs]
        offer = RTCSessionDescription(sdp=str(parsed), type="offer")
        self.assertCodecNames(offer, ["rtx", "VP8", "rtx", "H264", "rtx", "H264"])

        # Check the answer keeps every RTX codec, in the order of the offer.
        await pc2.setRemoteDescription(offer)
        pc2.addTrack(VideoStreamTrack())
        await pc2.setLocalDescription(await pc2.createAnswer())
        self.assertCodecNames(
            pc2.localDescription, ["rtx", "VP8", "rtx", "H264", "rtx", "H264"]
        )
        answer = SessionDescription.parse(pc2.localDescription.sdp)
        self.assertEqual(answer.media[0].fmt, media.fmt)

        # Record the receive parameters of both peers while connecting.
        with patch.object(
            RTCRtpReceiver,
            "receive",
            autospec=True,
            side_effect=RTCRtpReceiver.receive,
        ) as receive:
            await pc1.setRemoteDescription(pc2.localDescription)
            await self.assertIceCompleted(pc1, pc2)

            # Check both peers send video with VP8 rather than RTX.
            for tracks in [pc1_tracks, pc2_tracks]:
                self.assertEqual(len(tracks), 1)
                await asyncio.wait_for(tracks[0].recv(), timeout=5)

        # Check both peers map each RTX stream to the codec it repairs.
        self.assertEqual(receive.call_count, 2)
        for call in receive.call_args_list:
            parameters = call.args[1]
            self.assertEqual(
                [e.payloadType for e in parameters.encodings], [97, 99, 101]
            )
            for encoding in parameters.encodings:
                self.assertIsNotNone(encoding.rtx)

        # Close the connections.
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc2)

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
        self.assertCodecNames(
            pc1.localDescription, ["H264", "rtx", "H264", "rtx", "VP8", "rtx"]
        )

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
        self.assertCodecNames(
            pc2.localDescription, ["H264", "rtx", "H264", "rtx", "VP8", "rtx"]
        )

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
        self.assertCodecNames(pc1.localDescription, ["H264", "rtx", "H264", "rtx"])

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
        self.assertCodecNames(pc2.localDescription, ["H264", "rtx", "H264", "rtx"])

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
        await self.assertIceNew(pc2)
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

    async def _test_connect_datachannel_trickle_browser_string(self, with_mid: bool) -> None:
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

        # trickle candidates in browser string format
        for candidate in pc2.sctp.transport.transport.iceGatherer.getLocalCandidates():
            await pc1.addIceCandidate(RTCIceCandidate(
                candidate="candidate:" + candidate_to_sdp_string(candidate),
                sdpMid=pc2.sctp.mid if with_mid else None,
                sdpMLineIndex=None if with_mid else 0,
            ))
        for candidate in pc1.sctp.transport.transport.iceGatherer.getLocalCandidates():
            await pc2.addIceCandidate(RTCIceCandidate(
                candidate="candidate:" + candidate_to_sdp_string(candidate),
                sdpMid=pc1.sctp.mid if with_mid else None,
                sdpMLineIndex=None if with_mid else 0,
            ))

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
    async def test_connect_datachannel_trickle_browser_string_with_mid(self) -> None:
        await self._test_connect_datachannel_trickle_browser_string(with_mid=True)

    @asynctest
    async def test_connect_datachannel_trickle_browser_string_with_mline_index(self) -> None:
        await self._test_connect_datachannel_trickle_browser_string(with_mid=False)

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
    async def test_setLocalDescription_closed_during_gathering(self) -> None:
        pc = RTCPeerConnection()
        pc.addTrack(AudioStreamTrack())
        offer = await pc.createOffer()

        # The connection is closed while candidates are gathered, after the
        # description was applied.
        task = asyncio.ensure_future(pc.setLocalDescription(offer))
        await asyncio.sleep(0)
        await pc.close()
        await task
        self.assertEqual(pc.signalingState, "closed")

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
    async def test_setDescription_signalingstatechange(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTrack(AudioStreamTrack())
        pc2 = RTCPeerConnection()

        # The descriptions are updated before the signaling state changes
        # (W3C webrtc-pc, set the session description).
        events: list[tuple[str, str, Optional[str], Optional[str]]] = []

        def record(name: str, pc: RTCPeerConnection) -> None:
            @pc.on("signalingstatechange")
            def signalingstatechange() -> None:
                local = pc.localDescription
                remote = pc.remoteDescription
                events.append(
                    (
                        name,
                        pc.signalingState,
                        local.type if local else None,
                        remote.type if remote else None,
                    )
                )

        record("pc1", pc1)
        record("pc2", pc2)
        await negotiate(pc1, pc2)
        self.assertEqual(
            events,
            [
                ("pc1", "have-local-offer", "offer", None),
                ("pc2", "have-remote-offer", None, "offer"),
                ("pc2", "stable", "answer", "offer"),
                ("pc1", "stable", "offer", "answer"),
            ],
        )

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_extmap_attributes(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # Create an offer whose extensions carry attributes (RFC 6464, RFC 6904).
        pc1.addTrack(AudioStreamTrack())
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[0].extmap = [
            ExtmapDescription(id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"),
            ExtmapDescription(
                id=2,
                uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level",
                attributes="vad=on",
            ),
            ExtmapDescription(
                id=3,
                uri="urn:ietf:params:rtp-hdrext:encrypt",
                attributes="urn:ietf:params:rtp-hdrext:ssrc-audio-level",
            ),
        ]
        offer = RTCSessionDescription(sdp=str(parsed), type="offer")

        # Check the answer leaves out the encrypted extension.
        await pc2.setRemoteDescription(offer)
        await pc2.setLocalDescription(await pc2.createAnswer())
        answer = SessionDescription.parse(pc2.localDescription.sdp)
        self.assertEqual(
            answer.media[0].extmap,
            [
                ExtmapDescription(id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"),
                ExtmapDescription(
                    id=2, uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                ),
            ],
        )

        # Close the connections.
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_extmap_duplicate_uri(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # Create an offer with two instances of an extension (RFC 6464 section 4).
        pc1.addTrack(AudioStreamTrack())
        await pc1.setLocalDescription(await pc1.createOffer())
        parsed = SessionDescription.parse(pc1.localDescription.sdp)
        parsed.media[0].extmap = [
            ExtmapDescription(id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"),
            ExtmapDescription(
                id=2,
                uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level",
                attributes="vad=on",
            ),
            ExtmapDescription(
                id=5,
                uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level",
                attributes="vad=off",
            ),
        ]
        offer = RTCSessionDescription(sdp=str(parsed), type="offer")

        # Check the answer only accepts the first instance.
        await pc2.setRemoteDescription(offer)
        await pc2.setLocalDescription(await pc2.createAnswer())
        answer = SessionDescription.parse(pc2.localDescription.sdp)
        self.assertEqual(
            answer.media[0].extmap,
            [
                ExtmapDescription(id=1, uri="urn:ietf:params:rtp-hdrext:sdes:mid"),
                ExtmapDescription(
                    id=2, uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                ),
            ],
        )

        # Close the connections.
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

        # The answer rejects the "m=" section (RFC 9429 section 5.3.1).
        pc2_tracks = track_remote_tracks(pc2)
        await pc2.setRemoteDescription(mangled)
        answer = await pc2.createAnswer()
        self.assertEqual(media_ports(answer), [0])
        self.assertTrue(pc2.getTransceivers()[0].stopped)
        self.assertEqual(pc2_tracks, [])

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

        # The answer rejects the "m=" section (RFC 9429 section 5.3.1).
        pc2_tracks = track_remote_tracks(pc2)
        await pc2.setRemoteDescription(mangled)
        answer = await pc2.createAnswer()
        self.assertEqual(media_ports(answer), [0])
        self.assertTrue(pc2.getTransceivers()[0].stopped)
        self.assertEqual(pc2_tracks, [])

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_no_common_video_in_answer(self) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        pc1.addTrack(VideoStreamTrack())
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        answer = await pc2.createAnswer()

        # An answer without a supported format cannot be applied.
        mangled = RTCSessionDescription(
            sdp=answer.sdp.replace("90000", "92000"),
            type=answer.type,
        )
        with self.assertRaises(OperationError) as cm:
            await pc1.setRemoteDescription(mangled)
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
    async def test_setRemoteDescription_bundled_candidates_from_tagged_media(
        self,
    ) -> None:
        # Like a browser, pc1 offers each video section with its own candidates.
        pc1 = RTCPeerConnection(
            RTCConfiguration(bundlePolicy=RTCBundlePolicy.MAX_COMPAT)
        )
        pc2 = RTCPeerConnection()
        pc1.addTransceiver("video")
        pc1.addTransceiver("video")
        await pc1.setLocalDescription(await pc1.createOffer())
        self.assertTrue("a=group:BUNDLE 0 1" in pc1.localDescription.sdp)

        def ports(candidates: list[RTCIceCandidate]) -> list[int]:
            return sorted(c.port for c in candidates)

        tagged, other = [
            ports(t.sender.transport.transport.iceGatherer.getLocalCandidates())
            for t in pc1.getTransceivers()
        ]
        self.assertNotEqual(tagged, other)

        # pc2 must check the candidates of the BUNDLE-tagged section, "0".
        await pc2.setRemoteDescription(pc1.localDescription)
        iceTransport = pc2.getTransceivers()[0].receiver.transport.transport
        self.assertEqual(ports(iceTransport.getRemoteCandidates()), tagged)

        # close
        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_bundled_candidates_from_tag(self) -> None:
        pc1 = RTCPeerConnection()
        pc1_transceiver1 = pc1.addTransceiver("video")
        pc1_transceiver2 = pc1.addTransceiver("video")
        self.assertIs(
            pc1_transceiver1.receiver.transport, pc1_transceiver2.receiver.transport
        )
        await pc1.setLocalDescription(await pc1.createOffer())
        pc1_ice = pc1_transceiver1.receiver.transport.transport
        pc1_candidates = pc1_ice.iceGatherer.getLocalCandidates()
        self.assertTrue(pc1_candidates)

        pc2 = RTCPeerConnection()
        await pc2.setRemoteDescription(
            strip_ice_candidates_from_section(pc1.localDescription, 1)
        )
        pc2_transceiver1, pc2_transceiver2 = pc2.getTransceivers()
        self.assertIs(
            pc2_transceiver1.receiver.transport, pc2_transceiver2.receiver.transport
        )
        pc2_ice = pc2_transceiver1.receiver.transport.transport
        self.assertEqual(
            [(c.ip, c.port) for c in pc2_ice.getRemoteCandidates()],
            [(c.ip, c.port) for c in pc1_candidates],
        )

        await pc1.close()
        await pc2.close()

    @asynctest
    async def test_setRemoteDescription_bundled_same_kind_after_other_kind(
        self,
    ) -> None:
        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()
        pc1.addTransceiver("audio")
        pc1.addTransceiver("video")
        pc1.addTransceiver("video")

        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        await pc1.setRemoteDescription(pc2.localDescription)

        self.assertBundled(pc1)
        self.assertBundled(pc2)
        for pc in [pc1, pc2]:
            iceTransport = pc.getTransceivers()[0].receiver.transport.transport
            self.assertNotEqual(iceTransport.state, "closed")

        await pc1.close()
        await pc2.close()

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

    async def _test_connect_without_local_candidates(
        self, pc1: RTCPeerConnection
    ) -> None:
        # Without a TURN server, the relay policy leaves no local candidates.
        pc2 = RTCPeerConnection(
            RTCConfiguration(iceTransportPolicy=RTCIceTransportPolicy.RELAY)
        )
        pc2_states = track_states(pc2)

        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())
        self.assertNotIn("a=candidate:", pc2.localDescription.sdp)

        await self.sleepWhile(lambda: pc2.connectionState != "failed")
        self.assertEqual(pc2.iceConnectionState, "failed")

        # There is nothing to run DTLS over.
        transceivers = pc2.getTransceivers()
        dtlsTransport = (
            transceivers[0].receiver.transport if transceivers else pc2.sctp.transport
        )
        self.assertEqual(dtlsTransport.state, "new")

        # Close both peers.
        await pc1.close()
        await pc2.close()
        self.assertClosed(pc1)
        self.assertClosed(pc2)

        # Check the state changes.
        self.assertEqual(pc2_states["connectionState"], ["new", "failed", "closed"])
        self.assertEqual(pc2_states["iceConnectionState"], ["new", "failed", "closed"])

    @asynctest
    async def test_connect_audio_without_local_candidates(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTransceiver("audio")
        await self._test_connect_without_local_candidates(pc1)

    @asynctest
    async def test_connect_audio_without_local_candidates_trickle(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.addTransceiver("audio")
        pc2 = RTCPeerConnection(
            RTCConfiguration(iceTransportPolicy=RTCIceTransportPolicy.RELAY)
        )
        pc2_states = track_states(pc2)

        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(strip_ice_candidates(pc1.localDescription))
        await pc2.setLocalDescription(await pc2.createAnswer())
        await self.assertIceNew(pc2)

        # Remote candidates alone do not make a candidate pair.
        iceGatherer = pc1.getSenders()[0].transport.transport.iceGatherer
        for candidate in iceGatherer.getLocalCandidates():
            candidate.sdpMid = pc1.getTransceivers()[0].mid
            await pc2.addIceCandidate(candidate)
        await self.assertIceNew(pc2)

        # ICE can only fail once the remote candidates end.
        await pc2.addIceCandidate(None)
        await self.sleepWhile(lambda: pc2.iceConnectionState != "failed")
        self.assertEqual(pc2.iceConnectionState, "failed")

        # Close both peers.
        await pc1.close()
        await pc2.close()
        self.assertEqual(pc2_states["iceConnectionState"], ["new", "failed", "closed"])

    @asynctest
    async def test_connect_close_during_dtls_handshake(self) -> None:
        errors: list[dict[str, Any]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context)
        )

        pc1 = RTCPeerConnection()
        pc2 = RTCPeerConnection()

        # Both sections share a transport, which the second one must not restart.
        pc1.addTransceiver("audio")
        pc1.createDataChannel("chat")
        await pc1.setLocalDescription(await pc1.createOffer())
        await pc2.setRemoteDescription(pc1.localDescription)
        await pc2.setLocalDescription(await pc2.createAnswer())

        closed = asyncio.Event()

        async def close() -> None:
            await pc2.close()
            closed.set()

        @pc2.on("iceconnectionstatechange")
        def iceconnectionstatechange() -> None:
            if pc2.iceConnectionState == "completed":
                asyncio.ensure_future(close())

        await pc1.setRemoteDescription(pc2.localDescription)
        await asyncio.wait_for(closed.wait(), timeout=5)
        await asyncio.sleep(0.1)
        await pc1.close()

        # An exception nobody retrieved is reported when its task is collected.
        gc.collect()
        await asyncio.sleep(0)
        self.assertEqual(errors, [])

    @asynctest
    async def test_connect_datachannel_without_local_candidates(self) -> None:
        pc1 = RTCPeerConnection()
        pc1.createDataChannel("chat")
        await self._test_connect_without_local_candidates(pc1)

    @asynctest
    async def test_icetransportpolicy_relay(self) -> None:
        pc = RTCPeerConnection(
            RTCConfiguration(iceTransportPolicy=RTCIceTransportPolicy.RELAY)
        )

        # Audio and video use separate transports with the balanced bundle policy.
        pc.addTransceiver("audio")
        pc.addTransceiver("video")

        await pc.setLocalDescription(await pc.createOffer())
        self.assertEqual(pc.iceGatheringState, "complete")
        self.assertNotIn("a=candidate:", pc.localDescription.sdp)

        # close
        await pc.close()

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

        await pc1.close()
        await pc2.close()
