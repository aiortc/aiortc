# ruff: noqa: E501

from typing import Any
from unittest import TestCase

from aiortc.rtcrtpparameters import (
    RTCRtcpFeedback,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionParameters,
)
from aiortc.sdp import (
    GroupDescription,
    H264Level,
    H264Profile,
    SessionDescription,
    SsrcDescription,
    parse_h264_profile_level_id,
)

from .utils import lf2crlf


class SdpTest(TestCase):
    maxDiff = None

    def test_audio_chrome(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE audio
a=msid-semantic: WMS TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU
m=audio 45076 UDP/TLS/RTP/SAVPF 111 103 104 9 0 8 106 105 13 110 112 113 126
c=IN IP4 192.168.99.58
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host generation 0 network-id 2 network-cost 10
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host generation 0 network-id 1 network-cost 10
a=candidate:3496416974 1 tcp 1518283007 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active generation 0 network-id 2 network-cost 10
a=candidate:1936595596 1 tcp 1518214911 192.168.99.58 9 typ host tcptype active generation 0 network-id 1 network-cost 10
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=ice-options:trickle
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
a=mid:audio
a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=sendrecv
a=rtcp-mux
a=rtpmap:111 opus/48000/2
a=rtcp-fb:111 transport-cc
a=fmtp:111 minptime=10;useinbandfec=1
a=rtpmap:103 ISAC/16000
a=rtpmap:104 ISAC/32000
a=rtpmap:9 G722/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:106 CN/32000
a=rtpmap:105 CN/16000
a=rtpmap:13 CN/8000
a=rtpmap:110 telephone-event/48000
a=rtpmap:112 telephone-event/32000
a=rtpmap:113 telephone-event/16000
a=rtpmap:126 telephone-event/8000
a=ssrc:1944796561 cname:/vC4ULAr8vHNjXmq
a=ssrc:1944796561 msid:TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU ec1eb8de-8df8-4956-ae81-879e5d062d12
a=ssrc:1944796561 mslabel:TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU
a=ssrc:1944796561 label:ec1eb8de-8df8-4956-ae81-879e5d062d12"""
            )
        )

        self.assertEqual(
            d.group, [GroupDescription(semantic="BUNDLE", items=["audio"])]
        )
        self.assertEqual(
            d.msid_semantic,
            [
                GroupDescription(
                    semantic="WMS", items=["TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU"]
                )
            ],
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 863426017819471768 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45076)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/opus",
                    clockRate=48000,
                    channels=2,
                    payloadType=111,
                    rtcpFeedback=[RTCRtcpFeedback(type="transport-cc")],
                    parameters={"minptime": 10, "useinbandfec": 1},
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/ISAC", clockRate=16000, channels=1, payloadType=103
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/ISAC", clockRate=32000, channels=1, payloadType=104
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/G722", clockRate=8000, channels=1, payloadType=9
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/CN", clockRate=32000, channels=1, payloadType=106
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/CN", clockRate=16000, channels=1, payloadType=105
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/CN", clockRate=8000, channels=1, payloadType=13
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=48000,
                    channels=1,
                    payloadType=110,
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=32000,
                    channels=1,
                    payloadType=112,
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=16000,
                    channels=1,
                    payloadType=113,
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=8000,
                    channels=1,
                    payloadType=126,
                ),
            ],
        )
        self.assertEqual(
            d.media[0].rtp.headerExtensions,
            [
                RTCRtpHeaderExtensionParameters(
                    id=1, uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                )
            ],
        )
        self.assertEqual(d.media[0].rtp.muxId, "audio")
        self.assertEqual(d.media[0].rtcp_host, "0.0.0.0")
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # ssrc
        self.assertEqual(
            d.media[0].ssrc,
            [
                SsrcDescription(
                    ssrc=1944796561,
                    cname="/vC4ULAr8vHNjXmq",
                    msid="TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU ec1eb8de-8df8-4956-ae81-879e5d062d12",
                    mslabel="TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU",
                    label="ec1eb8de-8df8-4956-ae81-879e5d062d12",
                )
            ],
        )
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(
            d.media[0].fmt, [111, 103, 104, 9, 0, 8, 106, 105, 13, 110, 112, 113, 126]
        )
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice_candidates_complete, False)
        self.assertEqual(d.media[0].ice_options, "trickle")
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "5+Ix")
        self.assertEqual(d.media[0].ice.password, "uK8IlylxzDMUhrkVzdmj0M+v")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE audio
a=msid-semantic:WMS TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU
m=audio 45076 UDP/TLS/RTP/SAVPF 111 103 104 9 0 8 106 105 13 110 112 113 126
c=IN IP4 192.168.99.58
a=sendrecv
a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=mid:audio
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=ssrc:1944796561 cname:/vC4ULAr8vHNjXmq
a=ssrc:1944796561 msid:TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU ec1eb8de-8df8-4956-ae81-879e5d062d12
a=ssrc:1944796561 mslabel:TF6VRif1dxuAfe5uefrV2953LhUZt1keYvxU
a=ssrc:1944796561 label:ec1eb8de-8df8-4956-ae81-879e5d062d12
a=rtpmap:111 opus/48000/2
a=rtcp-fb:111 transport-cc
a=fmtp:111 minptime=10;useinbandfec=1
a=rtpmap:103 ISAC/16000
a=rtpmap:104 ISAC/32000
a=rtpmap:9 G722/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:106 CN/32000
a=rtpmap:105 CN/16000
a=rtpmap:13 CN/8000
a=rtpmap:110 telephone-event/48000
a=rtpmap:112 telephone-event/32000
a=rtpmap:113 telephone-event/16000
a=rtpmap:126 telephone-event/8000
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host
a=candidate:3496416974 1 tcp 1518283007 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=candidate:1936595596 1 tcp 1518214911 192.168.99.58 9 typ host tcptype active
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=ice-options:trickle
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
"""
            ),
        )

    def test_audio_firefox(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-58.0.1 4934139885953732403 1 IN IP4 0.0.0.0
s=-
t=0 0
a=sendrecv
a=fingerprint:sha-256 EB:A9:3E:50:D7:E3:B3:86:0F:7B:01:C1:EB:D6:AF:E4:97:DE:15:05:A8:DE:7B:83:56:C7:4B:6E:9D:75:D4:17
a=group:BUNDLE sdparta_0
a=ice-options:trickle
a=msid-semantic:WMS *
m=audio 45274 UDP/TLS/RTP/SAVPF 109 9 0 8 101
c=IN IP4 192.168.99.58
a=candidate:0 1 UDP 2122187007 192.168.99.58 45274 typ host
a=candidate:2 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 47387 typ host
a=candidate:3 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:4 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=candidate:0 2 UDP 2122187006 192.168.99.58 38612 typ host
a=candidate:2 2 UDP 2122252542 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 54301 typ host
a=candidate:3 2 TCP 2105458942 192.168.99.58 9 typ host tcptype active
a=candidate:4 2 TCP 2105524478 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=candidate:1 1 UDP 1685921791 1.2.3.4 37264 typ srflx raddr 192.168.99.58 rport 37264
a=candidate:1 2 UDP 1685921790 1.2.3.4 52902 typ srflx raddr 192.168.99.58 rport 52902
a=sendrecv
a=end-of-candidates
a=extmap:1/sendonly urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=extmap:2 urn:ietf:params:rtp-hdrext:sdes:mid
a=fmtp:109 maxplaybackrate=48000;stereo=1;useinbandfec=1
a=fmtp:101 0-15
a=ice-pwd:f9b83487285016f7492197a5790ceee5
a=ice-ufrag:403a81e1
a=ice-options:trickle
a=mid:sdparta_0
a=msid:{dee771c7-671a-451e-b847-f86f8e87c7d8} {12692dea-686c-47ca-b3e9-48f38fc92b78}
a=rtcp:38612 IN IP4 192.168.99.58
a=rtcp-mux
a=rtpmap:109 opus/48000/2
a=rtpmap:9 G722/8000/1
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=setup:actpass
a=ssrc:882128807 cname:{ed463ac5-dabf-44d4-8b9f-e14318427b2b}
"""
            )
        )
        self.assertEqual(
            d.group, [GroupDescription(semantic="BUNDLE", items=["sdparta_0"])]
        )
        self.assertEqual(
            d.msid_semantic, [GroupDescription(semantic="WMS", items=["*"])]
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(
            d.origin,
            "mozilla...THIS_IS_SDPARTA-58.0.1 4934139885953732403 1 IN IP4 0.0.0.0",
        )
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45274)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(
            d.media[0].msid,
            "{dee771c7-671a-451e-b847-f86f8e87c7d8} "
            "{12692dea-686c-47ca-b3e9-48f38fc92b78}",
        )
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/opus",
                    clockRate=48000,
                    channels=2,
                    payloadType=109,
                    parameters={
                        "maxplaybackrate": 48000,
                        "stereo": 1,
                        "useinbandfec": 1,
                    },
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/G722", clockRate=8000, channels=1, payloadType=9
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=8000,
                    channels=1,
                    payloadType=101,
                    parameters={"0-15": None},
                ),
            ],
        )
        self.assertEqual(
            d.media[0].rtp.headerExtensions,
            [
                RTCRtpHeaderExtensionParameters(
                    id=1, uri="urn:ietf:params:rtp-hdrext:ssrc-audio-level"
                ),
                RTCRtpHeaderExtensionParameters(
                    id=2, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.muxId, "sdparta_0")
        self.assertEqual(d.media[0].rtcp_host, "192.168.99.58")
        self.assertEqual(d.media[0].rtcp_port, 38612)
        self.assertEqual(d.media[0].rtcp_mux, True)
        self.assertEqual(
            d.webrtc_track_id(d.media[0]), "{12692dea-686c-47ca-b3e9-48f38fc92b78}"
        )

        # ssrc
        self.assertEqual(
            d.media[0].ssrc,
            [
                SsrcDescription(
                    ssrc=882128807, cname="{ed463ac5-dabf-44d4-8b9f-e14318427b2b}"
                )
            ],
        )
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [109, 9, 0, 8, 101])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 10)
        self.assertEqual(d.media[0].ice_candidates_complete, True)
        self.assertEqual(d.media[0].ice_options, "trickle")
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "403a81e1")
        self.assertEqual(d.media[0].ice.password, "f9b83487285016f7492197a5790ceee5")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "EB:A9:3E:50:D7:E3:B3:86:0F:7B:01:C1:EB:D6:AF:E4:97:DE:15:05:A8:DE:7B:83:56:C7:4B:6E:9D:75:D4:17",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-58.0.1 4934139885953732403 1 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE sdparta_0
a=msid-semantic:WMS *
m=audio 45274 UDP/TLS/RTP/SAVPF 109 9 0 8 101
c=IN IP4 192.168.99.58
a=sendrecv
a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=extmap:2 urn:ietf:params:rtp-hdrext:sdes:mid
a=mid:sdparta_0
a=msid:{dee771c7-671a-451e-b847-f86f8e87c7d8} {12692dea-686c-47ca-b3e9-48f38fc92b78}
a=rtcp:38612 IN IP4 192.168.99.58
a=rtcp-mux
a=ssrc:882128807 cname:{ed463ac5-dabf-44d4-8b9f-e14318427b2b}
a=rtpmap:109 opus/48000/2
a=fmtp:109 maxplaybackrate=48000;stereo=1;useinbandfec=1
a=rtpmap:9 G722/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-15
a=candidate:0 1 UDP 2122187007 192.168.99.58 45274 typ host
a=candidate:2 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 47387 typ host
a=candidate:3 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:4 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=candidate:0 2 UDP 2122187006 192.168.99.58 38612 typ host
a=candidate:2 2 UDP 2122252542 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 54301 typ host
a=candidate:3 2 TCP 2105458942 192.168.99.58 9 typ host tcptype active
a=candidate:4 2 TCP 2105524478 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=candidate:1 1 UDP 1685921791 1.2.3.4 37264 typ srflx raddr 192.168.99.58 rport 37264
a=candidate:1 2 UDP 1685921790 1.2.3.4 52902 typ srflx raddr 192.168.99.58 rport 52902
a=end-of-candidates
a=ice-ufrag:403a81e1
a=ice-pwd:f9b83487285016f7492197a5790ceee5
a=ice-options:trickle
a=fingerprint:sha-256 EB:A9:3E:50:D7:E3:B3:86:0F:7B:01:C1:EB:D6:AF:E4:97:DE:15:05:A8:DE:7B:83:56:C7:4B:6E:9D:75:D4:17
a=setup:actpass
"""
            ),
        )

    def test_audio_freeswitch(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=FreeSWITCH 1538380016 1538380017 IN IP4 1.2.3.4
s=FreeSWITCH
c=IN IP4 1.2.3.4
t=0 0
a=msid-semantic: WMS lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
m=audio 16628 UDP/TLS/RTP/SAVPF 8 101
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=ptime:20
a=fingerprint:sha-256 35:5A:BC:8E:CD:F8:CD:EB:36:00:BB:C4:C3:33:54:B5:9B:70:3C:E9:C4:33:8F:39:3C:4B:5B:5C:AD:88:12:2B
a=setup:active
a=rtcp-mux
a=rtcp:16628 IN IP4 1.2.3.4
a=ice-ufrag:75EDuLTEOkEUd3cu
a=ice-pwd:5dvb9SbfooWc49814CupdeTS
a=candidate:0560693492 1 udp 659136 1.2.3.4 16628 typ host generation 0
a=end-of-candidates
a=ssrc:2690029308 cname:rbaag6w9fGmRXQm6
a=ssrc:2690029308 msid:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys a0
a=ssrc:2690029308 mslabel:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
a=ssrc:2690029308 label:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ysa0"""
            )
        )

        self.assertEqual(d.group, [])
        self.assertEqual(
            d.msid_semantic,
            [
                GroupDescription(
                    semantic="WMS", items=["lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys"]
                )
            ],
        )
        self.assertEqual(d.host, "1.2.3.4")
        self.assertEqual(d.name, "FreeSWITCH")
        self.assertEqual(d.origin, "FreeSWITCH 1538380016 1538380017 IN IP4 1.2.3.4")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, None)
        self.assertEqual(d.media[0].port, 16628)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=8000,
                    channels=1,
                    payloadType=101,
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.headerExtensions, [])
        self.assertEqual(d.media[0].rtp.muxId, "")
        self.assertEqual(d.media[0].rtcp_host, "1.2.3.4")
        self.assertEqual(d.media[0].rtcp_port, 16628)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # ssrc
        self.assertEqual(
            d.media[0].ssrc,
            [
                SsrcDescription(
                    ssrc=2690029308,
                    cname="rbaag6w9fGmRXQm6",
                    msid="lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys a0",
                    mslabel="lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys",
                    label="lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ysa0",
                )
            ],
        )
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [8, 101])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 1)
        self.assertEqual(d.media[0].ice_candidates_complete, True)
        self.assertEqual(d.media[0].ice_options, None)
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "75EDuLTEOkEUd3cu")
        self.assertEqual(d.media[0].ice.password, "5dvb9SbfooWc49814CupdeTS")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "35:5A:BC:8E:CD:F8:CD:EB:36:00:BB:C4:C3:33:54:B5:9B:70:3C:E9:C4:33:8F:39:3C:4B:5B:5C:AD:88:12:2B",
        )
        self.assertEqual(d.media[0].dtls.role, "client")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=FreeSWITCH 1538380016 1538380017 IN IP4 1.2.3.4
s=FreeSWITCH
c=IN IP4 1.2.3.4
t=0 0
a=msid-semantic:WMS lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
m=audio 16628 UDP/TLS/RTP/SAVPF 8 101
a=rtcp:16628 IN IP4 1.2.3.4
a=rtcp-mux
a=ssrc:2690029308 cname:rbaag6w9fGmRXQm6
a=ssrc:2690029308 msid:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys a0
a=ssrc:2690029308 mslabel:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
a=ssrc:2690029308 label:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ysa0
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=candidate:0560693492 1 udp 659136 1.2.3.4 16628 typ host
a=end-of-candidates
a=ice-ufrag:75EDuLTEOkEUd3cu
a=ice-pwd:5dvb9SbfooWc49814CupdeTS
a=fingerprint:sha-256 35:5A:BC:8E:CD:F8:CD:EB:36:00:BB:C4:C3:33:54:B5:9B:70:3C:E9:C4:33:8F:39:3C:4B:5B:5C:AD:88:12:2B
a=setup:active
"""
            ),
        )

    def test_audio_freeswitch_no_dtls(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=FreeSWITCH 1538380016 1538380017 IN IP4 1.2.3.4
s=FreeSWITCH
c=IN IP4 1.2.3.4
t=0 0
a=msid-semantic: WMS lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
m=audio 16628 UDP/TLS/RTP/SAVPF 8 101
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=ptime:20
a=rtcp-mux
a=rtcp:16628 IN IP4 1.2.3.4
a=ice-ufrag:75EDuLTEOkEUd3cu
a=ice-pwd:5dvb9SbfooWc49814CupdeTS
a=candidate:0560693492 1 udp 659136 1.2.3.4 16628 typ host generation 0
a=end-of-candidates
a=ssrc:2690029308 cname:rbaag6w9fGmRXQm6
a=ssrc:2690029308 msid:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys a0
a=ssrc:2690029308 mslabel:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
a=ssrc:2690029308 label:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ysa0"""
            )
        )

        self.assertEqual(d.group, [])
        self.assertEqual(
            d.msid_semantic,
            [
                GroupDescription(
                    semantic="WMS", items=["lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys"]
                )
            ],
        )
        self.assertEqual(d.host, "1.2.3.4")
        self.assertEqual(d.name, "FreeSWITCH")
        self.assertEqual(d.origin, "FreeSWITCH 1538380016 1538380017 IN IP4 1.2.3.4")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, None)
        self.assertEqual(d.media[0].port, 16628)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/telephone-event",
                    clockRate=8000,
                    channels=1,
                    payloadType=101,
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.headerExtensions, [])
        self.assertEqual(d.media[0].rtp.muxId, "")
        self.assertEqual(d.media[0].rtcp_host, "1.2.3.4")
        self.assertEqual(d.media[0].rtcp_port, 16628)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # ssrc
        self.assertEqual(
            d.media[0].ssrc,
            [
                SsrcDescription(
                    ssrc=2690029308,
                    cname="rbaag6w9fGmRXQm6",
                    msid="lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys a0",
                    mslabel="lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys",
                    label="lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ysa0",
                )
            ],
        )
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [8, 101])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 1)
        self.assertEqual(d.media[0].ice_candidates_complete, True)
        self.assertEqual(d.media[0].ice_options, None)
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "75EDuLTEOkEUd3cu")
        self.assertEqual(d.media[0].ice.password, "5dvb9SbfooWc49814CupdeTS")

        # dtls
        self.assertEqual(d.media[0].dtls, None)

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=FreeSWITCH 1538380016 1538380017 IN IP4 1.2.3.4
s=FreeSWITCH
c=IN IP4 1.2.3.4
t=0 0
a=msid-semantic:WMS lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
m=audio 16628 UDP/TLS/RTP/SAVPF 8 101
a=rtcp:16628 IN IP4 1.2.3.4
a=rtcp-mux
a=ssrc:2690029308 cname:rbaag6w9fGmRXQm6
a=ssrc:2690029308 msid:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys a0
a=ssrc:2690029308 mslabel:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ys
a=ssrc:2690029308 label:lyNSTe6w2ijnMrDEiqTHFyhqjdAag3ysa0
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=candidate:0560693492 1 udp 659136 1.2.3.4 16628 typ host
a=end-of-candidates
a=ice-ufrag:75EDuLTEOkEUd3cu
a=ice-pwd:5dvb9SbfooWc49814CupdeTS
"""
            ),
        )

    def test_audio_dtls_session_level(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
m=audio 45076 UDP/TLS/RTP/SAVPF 0 8
c=IN IP4 192.168.99.58
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host generation 0 network-id 2 network-cost 10
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host generation 0 network-id 1 network-cost 10
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=mid:audio
a=sendrecv
a=rtcp-mux
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000"""
            )
        )

        self.assertEqual(d.group, [])
        self.assertEqual(d.msid_semantic, [])
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 863426017819471768 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45076)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.headerExtensions, [])
        self.assertEqual(d.media[0].rtp.muxId, "audio")
        self.assertEqual(d.media[0].rtcp_host, "0.0.0.0")
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # ssrc
        self.assertEqual(d.media[0].ssrc, [])
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [0, 8])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 2)
        self.assertEqual(d.media[0].ice_candidates_complete, False)
        self.assertEqual(d.media[0].ice_options, None)
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "5+Ix")
        self.assertEqual(d.media[0].ice.password, "uK8IlylxzDMUhrkVzdmj0M+v")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 45076 UDP/TLS/RTP/SAVPF 0 8
c=IN IP4 192.168.99.58
a=sendrecv
a=mid:audio
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
"""
            ),
        )

    def test_audio_ice_lite(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
a=ice-lite
m=audio 45076 UDP/TLS/RTP/SAVPF 0 8
c=IN IP4 192.168.99.58
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host generation 0 network-id 2 network-cost 10
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host generation 0 network-id 1 network-cost 10
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
a=mid:audio
a=sendrecv
a=rtcp-mux
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000"""
            )
        )

        self.assertEqual(d.group, [])
        self.assertEqual(d.msid_semantic, [])
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 863426017819471768 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45076)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.headerExtensions, [])
        self.assertEqual(d.media[0].rtp.muxId, "audio")
        self.assertEqual(d.media[0].rtcp_host, "0.0.0.0")
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # ssrc
        self.assertEqual(d.media[0].ssrc, [])
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [0, 8])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 2)
        self.assertEqual(d.media[0].ice_candidates_complete, False)
        self.assertEqual(d.media[0].ice_options, None)
        self.assertEqual(d.media[0].ice.iceLite, True)
        self.assertEqual(d.media[0].ice.usernameFragment, "5+Ix")
        self.assertEqual(d.media[0].ice.password, "uK8IlylxzDMUhrkVzdmj0M+v")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
a=ice-lite
m=audio 45076 UDP/TLS/RTP/SAVPF 0 8
c=IN IP4 192.168.99.58
a=sendrecv
a=mid:audio
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
"""
            ),
        )

    def test_audio_ice_session_level_credentials(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
m=audio 45076 UDP/TLS/RTP/SAVPF 0 8
c=IN IP4 192.168.99.58
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host generation 0 network-id 2 network-cost 10
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host generation 0 network-id 1 network-cost 10
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
a=mid:audio
a=sendrecv
a=rtcp-mux
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000"""
            )
        )

        self.assertEqual(d.group, [])
        self.assertEqual(d.msid_semantic, [])
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 863426017819471768 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45076)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
                RTCRtpCodecParameters(
                    mimeType="audio/PCMA", clockRate=8000, channels=1, payloadType=8
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.headerExtensions, [])
        self.assertEqual(d.media[0].rtp.muxId, "audio")
        self.assertEqual(d.media[0].rtcp_host, "0.0.0.0")
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # ssrc
        self.assertEqual(d.media[0].ssrc, [])
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [0, 8])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 2)
        self.assertEqual(d.media[0].ice_candidates_complete, False)
        self.assertEqual(d.media[0].ice_options, None)
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "5+Ix")
        self.assertEqual(d.media[0].ice.password, "uK8IlylxzDMUhrkVzdmj0M+v")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 45076 UDP/TLS/RTP/SAVPF 0 8
c=IN IP4 192.168.99.58
a=sendrecv
a=mid:audio
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
"""
            ),
        )

    def test_audio_rtcp_without_port(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 43580 RTP/AVP 0
c=IN IP4 192.168.99.58
a=sendrecv
a=rtcp:9
a=rtpmap:0 PCMU/8000
"""
            )
        )

        self.assertEqual(d.group, [])
        self.assertEqual(d.msid_semantic, [])
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 863426017819471768 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 43580)
        self.assertEqual(d.media[0].profile, "RTP/AVP")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="audio/PCMU", clockRate=8000, channels=1, payloadType=0
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.headerExtensions, [])
        self.assertEqual(d.media[0].rtp.muxId, "")
        self.assertEqual(d.media[0].rtcp_host, None)
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, False)

        # ssrc
        self.assertEqual(d.media[0].ssrc, [])
        self.assertEqual(d.media[0].ssrc_group, [])

        # formats
        self.assertEqual(d.media[0].fmt, [0])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 0)

        # dtls
        self.assertEqual(d.media[0].dtls, None)

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=- 863426017819471768 2 IN IP4 127.0.0.1
s=-
t=0 0
m=audio 43580 RTP/AVP 0
c=IN IP4 192.168.99.58
a=sendrecv
a=rtcp:9
a=rtpmap:0 PCMU/8000
"""
            ),
        )

    def test_datachannel_firefox(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-58.0.1 7514673380034989017 0 IN IP4 0.0.0.0
s=-
t=0 0
a=sendrecv
a=fingerprint:sha-256 39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74
a=group:BUNDLE sdparta_0
a=ice-options:trickle
a=msid-semantic:WMS *
m=application 45791 DTLS/SCTP 5000
c=IN IP4 192.168.99.58
a=candidate:0 1 UDP 2122187007 192.168.99.58 45791 typ host
a=candidate:1 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 44087 typ host
a=candidate:2 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:3 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=sendrecv
a=end-of-candidates
a=ice-pwd:d30a5aec4dd81f07d4ff3344209400ab
a=ice-ufrag:9889e0c4
a=mid:sdparta_0
a=sctpmap:5000 webrtc-datachannel 256
a=setup:actpass
a=max-message-size:1073741823
"""
            )
        )

        self.assertEqual(
            d.group, [GroupDescription(semantic="BUNDLE", items=["sdparta_0"])]
        )
        self.assertEqual(
            d.msid_semantic, [GroupDescription(semantic="WMS", items=["*"])]
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(
            d.origin,
            "mozilla...THIS_IS_SDPARTA-58.0.1 7514673380034989017 0 IN IP4 0.0.0.0",
        )
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "application")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45791)
        self.assertEqual(d.media[0].profile, "DTLS/SCTP")
        self.assertEqual(d.media[0].fmt, ["5000"])

        # sctp
        self.assertEqual(d.media[0].sctpmap, {5000: "webrtc-datachannel 256"})
        self.assertEqual(d.media[0].sctp_port, None)
        self.assertIsNotNone(d.media[0].sctpCapabilities)
        self.assertEqual(d.media[0].sctpCapabilities.maxMessageSize, 1073741823)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice_candidates_complete, True)
        self.assertEqual(d.media[0].ice_options, "trickle")
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "9889e0c4")
        self.assertEqual(d.media[0].ice.password, "d30a5aec4dd81f07d4ff3344209400ab")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-58.0.1 7514673380034989017 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE sdparta_0
a=msid-semantic:WMS *
m=application 45791 DTLS/SCTP 5000
c=IN IP4 192.168.99.58
a=sendrecv
a=mid:sdparta_0
a=sctpmap:5000 webrtc-datachannel 256
a=max-message-size:1073741823
a=candidate:0 1 UDP 2122187007 192.168.99.58 45791 typ host
a=candidate:1 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 44087 typ host
a=candidate:2 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:3 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=end-of-candidates
a=ice-ufrag:9889e0c4
a=ice-pwd:d30a5aec4dd81f07d4ff3344209400ab
a=ice-options:trickle
a=fingerprint:sha-256 39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74
a=setup:actpass
"""
            ),
        )

    def test_datachannel_firefox_63(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-58.0.1 7514673380034989017 0 IN IP4 0.0.0.0
s=-
t=0 0
a=sendrecv
a=fingerprint:sha-256 39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74
a=group:BUNDLE sdparta_0
a=ice-options:trickle
a=msid-semantic:WMS *
m=application 45791 UDP/DTLS/SCTP webrtc-datachannel
c=IN IP4 192.168.99.58
a=candidate:0 1 UDP 2122187007 192.168.99.58 45791 typ host
a=candidate:1 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 44087 typ host
a=candidate:2 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:3 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=sendrecv
a=end-of-candidates
a=ice-pwd:d30a5aec4dd81f07d4ff3344209400ab
a=ice-ufrag:9889e0c4
a=mid:sdparta_0
a=sctp-port:5000
a=setup:actpass
a=max-message-size:1073741823
"""
            )
        )

        self.assertEqual(
            d.group, [GroupDescription(semantic="BUNDLE", items=["sdparta_0"])]
        )
        self.assertEqual(
            d.msid_semantic, [GroupDescription(semantic="WMS", items=["*"])]
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(
            d.origin,
            "mozilla...THIS_IS_SDPARTA-58.0.1 7514673380034989017 0 IN IP4 0.0.0.0",
        )
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "application")
        self.assertEqual(d.media[0].host, "192.168.99.58")
        self.assertEqual(d.media[0].port, 45791)
        self.assertEqual(d.media[0].profile, "UDP/DTLS/SCTP")
        self.assertEqual(d.media[0].fmt, ["webrtc-datachannel"])

        # sctp
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, 5000)
        self.assertIsNotNone(d.media[0].sctpCapabilities)
        self.assertEqual(d.media[0].sctpCapabilities.maxMessageSize, 1073741823)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice_candidates_complete, True)
        self.assertEqual(d.media[0].ice_options, "trickle")
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "9889e0c4")
        self.assertEqual(d.media[0].ice.password, "d30a5aec4dd81f07d4ff3344209400ab")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-58.0.1 7514673380034989017 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE sdparta_0
a=msid-semantic:WMS *
m=application 45791 UDP/DTLS/SCTP webrtc-datachannel
c=IN IP4 192.168.99.58
a=sendrecv
a=mid:sdparta_0
a=sctp-port:5000
a=max-message-size:1073741823
a=candidate:0 1 UDP 2122187007 192.168.99.58 45791 typ host
a=candidate:1 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 44087 typ host
a=candidate:2 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:3 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=end-of-candidates
a=ice-ufrag:9889e0c4
a=ice-pwd:d30a5aec4dd81f07d4ff3344209400ab
a=ice-options:trickle
a=fingerprint:sha-256 39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74
a=setup:actpass
"""
            ),
        )

    def test_video_chrome(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=- 5195484278799753993 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE video
a=msid-semantic: WMS bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ
m=video 34955 UDP/TLS/RTP/SAVPF 96 97 98 99 100 101 102
c=IN IP4 10.101.2.67
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:638323114 1 udp 2122260223 10.101.2.67 34955 typ host generation 0 network-id 2 network-cost 10
a=candidate:1754264922 1 tcp 1518280447 10.101.2.67 9 typ host tcptype active generation 0 network-id 2 network-cost 10
a=ice-ufrag:9KhP
a=ice-pwd:mlPea2xBCmFmNLfmy/jlqw1D
a=ice-options:trickle
a=fingerprint:sha-256 30:4A:BF:65:23:D1:99:AB:AE:9F:FD:5D:B1:08:4F:09:7C:9F:F2:CC:50:16:13:81:1B:5D:DD:D0:98:45:81:1E
a=setup:actpass
a=mid:video
a=extmap:2 urn:ietf:params:rtp-hdrext:toffset
a=extmap:3 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:4 urn:3gpp:video-orientation
a=extmap:5 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
a=extmap:6 http://www.webrtc.org/experiments/rtp-hdrext/playout-delay
a=extmap:7 http://www.webrtc.org/experiments/rtp-hdrext/video-content-type
a=extmap:8 http://www.webrtc.org/experiments/rtp-hdrext/video-timing
a=sendrecv
a=rtcp-mux
a=rtcp-rsize
a=rtpmap:96 VP8/90000
a=rtcp-fb:96 goog-remb
a=rtcp-fb:96 transport-cc
a=rtcp-fb:96 ccm fir
a=rtcp-fb:96 nack
a=rtcp-fb:96 nack pli
a=rtpmap:97 rtx/90000
a=fmtp:97 apt=96
a=rtpmap:98 VP9/90000
a=rtcp-fb:98 goog-remb
a=rtcp-fb:98 transport-cc
a=rtcp-fb:98 ccm fir
a=rtcp-fb:98 nack
a=rtcp-fb:98 nack pli
a=rtpmap:99 rtx/90000
a=fmtp:99 apt=98
a=rtpmap:100 red/90000
a=rtpmap:101 rtx/90000
a=fmtp:101 apt=100
a=rtpmap:102 ulpfec/90000
a=ssrc-group:FID 1845476211 3305256354
a=ssrc:1845476211 cname:9iW3jspLCZJ5WjOZ
a=ssrc:1845476211 msid:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ 420c6f28-439d-4ead-b93c-94e14c0a16b4
a=ssrc:1845476211 mslabel:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ
a=ssrc:1845476211 label:420c6f28-439d-4ead-b93c-94e14c0a16b4
a=ssrc:3305256354 cname:9iW3jspLCZJ5WjOZ
a=ssrc:3305256354 msid:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ 420c6f28-439d-4ead-b93c-94e14c0a16b4
a=ssrc:3305256354 mslabel:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ
a=ssrc:3305256354 label:420c6f28-439d-4ead-b93c-94e14c0a16b4
"""
            )
        )

        self.assertEqual(
            d.group, [GroupDescription(semantic="BUNDLE", items=["video"])]
        )
        self.assertEqual(
            d.msid_semantic,
            [
                GroupDescription(
                    semantic="WMS", items=["bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ"]
                )
            ],
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 5195484278799753993 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "video")
        self.assertEqual(d.media[0].host, "10.101.2.67")
        self.assertEqual(d.media[0].port, 34955)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="video/VP8",
                    clockRate=90000,
                    payloadType=96,
                    rtcpFeedback=[
                        RTCRtcpFeedback(type="goog-remb"),
                        RTCRtcpFeedback(type="transport-cc"),
                        RTCRtcpFeedback(type="ccm", parameter="fir"),
                        RTCRtcpFeedback(type="nack"),
                        RTCRtcpFeedback(type="nack", parameter="pli"),
                    ],
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=97,
                    parameters={"apt": 96},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/VP9",
                    clockRate=90000,
                    payloadType=98,
                    rtcpFeedback=[
                        RTCRtcpFeedback(type="goog-remb"),
                        RTCRtcpFeedback(type="transport-cc"),
                        RTCRtcpFeedback(type="ccm", parameter="fir"),
                        RTCRtcpFeedback(type="nack"),
                        RTCRtcpFeedback(type="nack", parameter="pli"),
                    ],
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=99,
                    parameters={"apt": 98},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/red", clockRate=90000, payloadType=100
                ),
                RTCRtpCodecParameters(
                    mimeType="video/rtx",
                    clockRate=90000,
                    payloadType=101,
                    parameters={"apt": 100},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/ulpfec", clockRate=90000, payloadType=102
                ),
            ],
        )
        self.assertEqual(
            d.media[0].rtp.headerExtensions,
            [
                RTCRtpHeaderExtensionParameters(
                    id=2, uri="urn:ietf:params:rtp-hdrext:toffset"
                ),
                RTCRtpHeaderExtensionParameters(
                    id=3,
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
                ),
                RTCRtpHeaderExtensionParameters(id=4, uri="urn:3gpp:video-orientation"),
                RTCRtpHeaderExtensionParameters(
                    id=5,
                    uri="http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
                ),
                RTCRtpHeaderExtensionParameters(
                    id=6,
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/playout-delay",
                ),
                RTCRtpHeaderExtensionParameters(
                    id=7,
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/video-content-type",
                ),
                RTCRtpHeaderExtensionParameters(
                    id=8,
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/video-timing",
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.muxId, "video")
        self.assertEqual(d.media[0].rtcp_host, "0.0.0.0")
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, True)
        self.assertEqual(d.webrtc_track_id(d.media[0]), None)

        # ssrc
        self.assertEqual(
            d.media[0].ssrc,
            [
                SsrcDescription(
                    ssrc=1845476211,
                    cname="9iW3jspLCZJ5WjOZ",
                    msid="bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ 420c6f28-439d-4ead-b93c-94e14c0a16b4",
                    mslabel="bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ",
                    label="420c6f28-439d-4ead-b93c-94e14c0a16b4",
                ),
                SsrcDescription(
                    ssrc=3305256354,
                    cname="9iW3jspLCZJ5WjOZ",
                    msid="bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ 420c6f28-439d-4ead-b93c-94e14c0a16b4",
                    mslabel="bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ",
                    label="420c6f28-439d-4ead-b93c-94e14c0a16b4",
                ),
            ],
        )
        self.assertEqual(
            d.media[0].ssrc_group,
            [GroupDescription(semantic="FID", items=[1845476211, 3305256354])],
        )

        # formats
        self.assertEqual(d.media[0].fmt, [96, 97, 98, 99, 100, 101, 102])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 2)
        self.assertEqual(d.media[0].ice_candidates_complete, False)
        self.assertEqual(d.media[0].ice_options, "trickle")
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "9KhP")
        self.assertEqual(d.media[0].ice.password, "mlPea2xBCmFmNLfmy/jlqw1D")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "30:4A:BF:65:23:D1:99:AB:AE:9F:FD:5D:B1:08:4F:09:7C:9F:F2:CC:50:16:13:81:1B:5D:DD:D0:98:45:81:1E",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=- 5195484278799753993 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE video
a=msid-semantic:WMS bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ
m=video 34955 UDP/TLS/RTP/SAVPF 96 97 98 99 100 101 102
c=IN IP4 10.101.2.67
a=sendrecv
a=extmap:2 urn:ietf:params:rtp-hdrext:toffset
a=extmap:3 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:4 urn:3gpp:video-orientation
a=extmap:5 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
a=extmap:6 http://www.webrtc.org/experiments/rtp-hdrext/playout-delay
a=extmap:7 http://www.webrtc.org/experiments/rtp-hdrext/video-content-type
a=extmap:8 http://www.webrtc.org/experiments/rtp-hdrext/video-timing
a=mid:video
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=ssrc-group:FID 1845476211 3305256354
a=ssrc:1845476211 cname:9iW3jspLCZJ5WjOZ
a=ssrc:1845476211 msid:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ 420c6f28-439d-4ead-b93c-94e14c0a16b4
a=ssrc:1845476211 mslabel:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ
a=ssrc:1845476211 label:420c6f28-439d-4ead-b93c-94e14c0a16b4
a=ssrc:3305256354 cname:9iW3jspLCZJ5WjOZ
a=ssrc:3305256354 msid:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ 420c6f28-439d-4ead-b93c-94e14c0a16b4
a=ssrc:3305256354 mslabel:bbgewhUzS6hvFDlSlrhQ6zYlwW7ttRrK8QeQ
a=ssrc:3305256354 label:420c6f28-439d-4ead-b93c-94e14c0a16b4
a=rtpmap:96 VP8/90000
a=rtcp-fb:96 goog-remb
a=rtcp-fb:96 transport-cc
a=rtcp-fb:96 ccm fir
a=rtcp-fb:96 nack
a=rtcp-fb:96 nack pli
a=rtpmap:97 rtx/90000
a=fmtp:97 apt=96
a=rtpmap:98 VP9/90000
a=rtcp-fb:98 goog-remb
a=rtcp-fb:98 transport-cc
a=rtcp-fb:98 ccm fir
a=rtcp-fb:98 nack
a=rtcp-fb:98 nack pli
a=rtpmap:99 rtx/90000
a=fmtp:99 apt=98
a=rtpmap:100 red/90000
a=rtpmap:101 rtx/90000
a=fmtp:101 apt=100
a=rtpmap:102 ulpfec/90000
a=candidate:638323114 1 udp 2122260223 10.101.2.67 34955 typ host
a=candidate:1754264922 1 tcp 1518280447 10.101.2.67 9 typ host tcptype active
a=ice-ufrag:9KhP
a=ice-pwd:mlPea2xBCmFmNLfmy/jlqw1D
a=ice-options:trickle
a=fingerprint:sha-256 30:4A:BF:65:23:D1:99:AB:AE:9F:FD:5D:B1:08:4F:09:7C:9F:F2:CC:50:16:13:81:1B:5D:DD:D0:98:45:81:1E
a=setup:actpass
"""
            ),
        )

    def test_video_firefox(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-61.0 8964514366714082732 0 IN IP4 0.0.0.0
s=-
t=0 0
a=sendrecv
a=fingerprint:sha-256 AF:9E:29:99:AC:F6:F6:A2:86:A7:2E:A5:83:94:21:7F:F1:39:C5:E3:8F:E4:08:04:D9:D8:70:6D:6C:A2:A1:D5
a=group:BUNDLE sdparta_0
a=ice-options:trickle
a=msid-semantic:WMS *
m=video 42738 UDP/TLS/RTP/SAVPF 120 121
c=IN IP4 192.168.99.7
a=candidate:0 1 UDP 2122252543 192.168.99.7 42738 typ host
a=candidate:1 1 TCP 2105524479 192.168.99.7 9 typ host tcptype active
a=candidate:0 2 UDP 2122252542 192.168.99.7 52914 typ host
a=candidate:1 2 TCP 2105524478 192.168.99.7 9 typ host tcptype active
a=sendrecv
a=end-of-candidates
a=extmap:3 urn:ietf:params:rtp-hdrext:sdes:mid
a=extmap:4 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:5 urn:ietf:params:rtp-hdrext:toffset
a=fmtp:120 max-fs=12288;max-fr=60
a=fmtp:121 max-fs=12288;max-fr=60
a=ice-pwd:c43b0306087bb4de15f70e4405c4dafe
a=ice-ufrag:1a0e6b24
a=mid:sdparta_0
a=msid:{38c9a1f0-d360-4ad8-afe3-4d7f6d4ae4e1} {d27161f3-ab5d-4aff-9dd8-4a24bfbe56d4}
a=rtcp:52914 IN IP4 192.168.99.7
a=rtcp-fb:120 nack
a=rtcp-fb:120 nack pli
a=rtcp-fb:120 ccm fir
a=rtcp-fb:120 goog-remb
a=rtcp-fb:121 nack
a=rtcp-fb:121 nack pli
a=rtcp-fb:121 ccm fir
a=rtcp-fb:121 goog-remb
a=rtcp-mux
a=rtpmap:120 VP8/90000
a=rtpmap:121 VP9/90000
a=setup:actpass
a=ssrc:3408404552 cname:{6f52d07e-17ef-42c5-932b-3b57c64fe049}
"""
            )
        )

        self.assertEqual(
            d.group, [GroupDescription(semantic="BUNDLE", items=["sdparta_0"])]
        )
        self.assertEqual(
            d.msid_semantic, [GroupDescription(semantic="WMS", items=["*"])]
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(
            d.origin,
            "mozilla...THIS_IS_SDPARTA-61.0 8964514366714082732 0 IN IP4 0.0.0.0",
        )
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, "video")
        self.assertEqual(d.media[0].host, "192.168.99.7")
        self.assertEqual(d.media[0].port, 42738)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(
            d.media[0].msid,
            "{38c9a1f0-d360-4ad8-afe3-4d7f6d4ae4e1} "
            "{d27161f3-ab5d-4aff-9dd8-4a24bfbe56d4}",
        )
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="video/VP8",
                    clockRate=90000,
                    payloadType=120,
                    rtcpFeedback=[
                        RTCRtcpFeedback(type="nack"),
                        RTCRtcpFeedback(type="nack", parameter="pli"),
                        RTCRtcpFeedback(type="ccm", parameter="fir"),
                        RTCRtcpFeedback(type="goog-remb"),
                    ],
                    parameters={"max-fs": 12288, "max-fr": 60},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/VP9",
                    clockRate=90000,
                    payloadType=121,
                    rtcpFeedback=[
                        RTCRtcpFeedback(type="nack"),
                        RTCRtcpFeedback(type="nack", parameter="pli"),
                        RTCRtcpFeedback(type="ccm", parameter="fir"),
                        RTCRtcpFeedback(type="goog-remb"),
                    ],
                    parameters={"max-fs": 12288, "max-fr": 60},
                ),
            ],
        )
        self.assertEqual(
            d.media[0].rtp.headerExtensions,
            [
                RTCRtpHeaderExtensionParameters(
                    id=3, uri="urn:ietf:params:rtp-hdrext:sdes:mid"
                ),
                RTCRtpHeaderExtensionParameters(
                    id=4,
                    uri="http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
                ),
                RTCRtpHeaderExtensionParameters(
                    id=5, uri="urn:ietf:params:rtp-hdrext:toffset"
                ),
            ],
        )
        self.assertEqual(d.media[0].rtp.muxId, "sdparta_0")
        self.assertEqual(d.media[0].rtcp_host, "192.168.99.7")
        self.assertEqual(d.media[0].rtcp_port, 52914)
        self.assertEqual(d.media[0].rtcp_mux, True)
        self.assertEqual(
            d.webrtc_track_id(d.media[0]), "{d27161f3-ab5d-4aff-9dd8-4a24bfbe56d4}"
        )

        # formats
        self.assertEqual(d.media[0].fmt, [120, 121])
        self.assertEqual(d.media[0].sctpmap, {})
        self.assertEqual(d.media[0].sctp_port, None)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice_candidates_complete, True)
        self.assertEqual(d.media[0].ice_options, "trickle")
        self.assertEqual(d.media[0].ice.iceLite, False)
        self.assertEqual(d.media[0].ice.usernameFragment, "1a0e6b24")
        self.assertEqual(d.media[0].ice.password, "c43b0306087bb4de15f70e4405c4dafe")

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, "sha-256")
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            "AF:9E:29:99:AC:F6:F6:A2:86:A7:2E:A5:83:94:21:7F:F1:39:C5:E3:8F:E4:08:04:D9:D8:70:6D:6C:A2:A1:D5",
        )
        self.assertEqual(d.media[0].dtls.role, "auto")

        self.assertEqual(
            str(d),
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-61.0 8964514366714082732 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE sdparta_0
a=msid-semantic:WMS *
m=video 42738 UDP/TLS/RTP/SAVPF 120 121
c=IN IP4 192.168.99.7
a=sendrecv
a=extmap:3 urn:ietf:params:rtp-hdrext:sdes:mid
a=extmap:4 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:5 urn:ietf:params:rtp-hdrext:toffset
a=mid:sdparta_0
a=msid:{38c9a1f0-d360-4ad8-afe3-4d7f6d4ae4e1} {d27161f3-ab5d-4aff-9dd8-4a24bfbe56d4}
a=rtcp:52914 IN IP4 192.168.99.7
a=rtcp-mux
a=ssrc:3408404552 cname:{6f52d07e-17ef-42c5-932b-3b57c64fe049}
a=rtpmap:120 VP8/90000
a=rtcp-fb:120 nack
a=rtcp-fb:120 nack pli
a=rtcp-fb:120 ccm fir
a=rtcp-fb:120 goog-remb
a=fmtp:120 max-fs=12288;max-fr=60
a=rtpmap:121 VP9/90000
a=rtcp-fb:121 nack
a=rtcp-fb:121 nack pli
a=rtcp-fb:121 ccm fir
a=rtcp-fb:121 goog-remb
a=fmtp:121 max-fs=12288;max-fr=60
a=candidate:0 1 UDP 2122252543 192.168.99.7 42738 typ host
a=candidate:1 1 TCP 2105524479 192.168.99.7 9 typ host tcptype active
a=candidate:0 2 UDP 2122252542 192.168.99.7 52914 typ host
a=candidate:1 2 TCP 2105524478 192.168.99.7 9 typ host tcptype active
a=end-of-candidates
a=ice-ufrag:1a0e6b24
a=ice-pwd:c43b0306087bb4de15f70e4405c4dafe
a=ice-options:trickle
a=fingerprint:sha-256 AF:9E:29:99:AC:F6:F6:A2:86:A7:2E:A5:83:94:21:7F:F1:39:C5:E3:8F:E4:08:04:D9:D8:70:6D:6C:A2:A1:D5
a=setup:actpass
"""
            ),
        )

    def test_video_session_star_rtcp_fb(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """v=0
o=mozilla...THIS_IS_SDPARTA-61.0 8964514366714082732 0 IN IP4 0.0.0.0
s=-
t=0 0
a=group:BUNDLE sdparta_0
a=msid-semantic:WMS *
m=video 42738 UDP/TLS/RTP/SAVPF 120 121
c=IN IP4 192.168.99.7
a=sendrecv
a=extmap:3 urn:ietf:params:rtp-hdrext:sdes:mid
a=extmap:4 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:5 urn:ietf:params:rtp-hdrext:toffset
a=mid:sdparta_0
a=msid:{38c9a1f0-d360-4ad8-afe3-4d7f6d4ae4e1} {d27161f3-ab5d-4aff-9dd8-4a24bfbe56d4}
a=rtcp:52914 IN IP4 192.168.99.7
a=rtcp-mux
a=ssrc:3408404552 cname:{6f52d07e-17ef-42c5-932b-3b57c64fe049}
a=rtpmap:120 VP8/90000
a=fmtp:120 max-fs=12288;max-fr=60
a=rtpmap:121 VP9/90000
a=fmtp:121 max-fs=12288;max-fr=60
a=rtcp-fb:* nack
a=rtcp-fb:* nack pli
a=rtcp-fb:* goog-remb
a=candidate:0 1 UDP 2122252543 192.168.99.7 42738 typ host
a=candidate:1 1 TCP 2105524479 192.168.99.7 9 typ host tcptype active
a=candidate:0 2 UDP 2122252542 192.168.99.7 52914 typ host
a=candidate:1 2 TCP 2105524478 192.168.99.7 9 typ host tcptype active
a=end-of-candidates
a=ice-ufrag:1a0e6b24
a=ice-pwd:c43b0306087bb4de15f70e4405c4dafe
a=ice-options:trickle
a=fingerprint:sha-256 AF:9E:29:99:AC:F6:F6:A2:86:A7:2E:A5:83:94:21:7F:F1:39:C5:E3:8F:E4:08:04:D9:D8:70:6D:6C:A2:A1:D5
a=setup:actpass
"""
            )
        )
        self.assertEqual(
            d.media[0].rtp.codecs,
            [
                RTCRtpCodecParameters(
                    mimeType="video/VP8",
                    clockRate=90000,
                    payloadType=120,
                    rtcpFeedback=[
                        RTCRtcpFeedback(type="nack"),
                        RTCRtcpFeedback(type="nack", parameter="pli"),
                        RTCRtcpFeedback(type="goog-remb"),
                    ],
                    parameters={"max-fs": 12288, "max-fr": 60},
                ),
                RTCRtpCodecParameters(
                    mimeType="video/VP9",
                    clockRate=90000,
                    payloadType=121,
                    rtcpFeedback=[
                        RTCRtcpFeedback(type="nack"),
                        RTCRtcpFeedback(type="nack", parameter="pli"),
                        RTCRtcpFeedback(type="goog-remb"),
                    ],
                    parameters={"max-fs": 12288, "max-fr": 60},
                ),
            ],
        )

    def test_safari(self) -> None:
        d = SessionDescription.parse(
            lf2crlf(
                """
v=0
o=- 8148572839875102105 2 IN IP4 127.0.0.1
s=-
t=0 0
a=group:BUNDLE audio video data
a=msid-semantic: WMS cb7e185b-6110-4f65-b027-ddb8b5fa78c7
m=audio 61015 UDP/TLS/RTP/SAVPF 111 103 9 102 0 8 105 13 110 113 126
c=IN IP4 1.2.3.4
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:3317362580 1 udp 2113937151 192.168.0.87 61015 typ host generation 0 network-cost 999
a=candidate:3103151263 1 udp 2113939711 2a01:e0a:151:dc10:a8cb:5e93:9627:557c 61016 typ host generation 0 network-cost 999
a=candidate:842163049 1 udp 1677729535 1.2.3.4 61015 typ srflx raddr 192.168.0.87 rport 61015 generation 0 network-cost 999
a=ice-ufrag:XSmV
a=ice-pwd:Ss5xY4RMFEJASRvK5TIPgLN9
a=ice-options:trickle
a=fingerprint:sha-256 F2:68:A5:17:E7:85:D6:4E:23:F1:5D:02:39:9E:0F:B5:EA:C0:BD:FC:F5:27:3E:38:9B:BA:4E:AF:8B:35:AF:89
a=setup:actpass
a=mid:audio
a=extmap:1 urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=sendrecv
a=rtcp-mux
a=rtpmap:111 opus/48000/2
a=rtcp-fb:111 transport-cc
a=fmtp:111 minptime=10;useinbandfec=1
a=rtpmap:103 ISAC/16000
a=rtpmap:9 G722/8000
a=rtpmap:102 ILBC/8000
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:105 CN/16000
a=rtpmap:13 CN/8000
a=rtpmap:110 telephone-event/48000
a=rtpmap:113 telephone-event/16000
a=rtpmap:126 telephone-event/8000
a=ssrc:205815247 cname:JTNiIZ6eJ7ghkHaB
a=ssrc:205815247 msid:cb7e185b-6110-4f65-b027-ddb8b5fa78c7 f473166a-7fe5-4ab6-a3af-c5eb806a13b9
a=ssrc:205815247 mslabel:cb7e185b-6110-4f65-b027-ddb8b5fa78c7
a=ssrc:205815247 label:f473166a-7fe5-4ab6-a3af-c5eb806a13b9
m=video 51044 UDP/TLS/RTP/SAVPF 96 97 98 99 100 101 127 125 104
c=IN IP4 1.2.3.4
a=rtcp:9 IN IP4 0.0.0.0
a=candidate:3317362580 1 udp 2113937151 192.168.0.87 51044 typ host generation 0 network-cost 999
a=candidate:3103151263 1 udp 2113939711 2a01:e0a:151:dc10:a8cb:5e93:9627:557c 51045 typ host generation 0 network-cost 999
a=candidate:842163049 1 udp 1677729535 82.64.133.208 51044 typ srflx raddr 192.168.0.87 rport 51044 generation 0 network-cost 999
a=ice-ufrag:XSmV
a=ice-pwd:Ss5xY4RMFEJASRvK5TIPgLN9
a=ice-options:trickle
a=fingerprint:sha-256 F2:68:A5:17:E7:85:D6:4E:23:F1:5D:02:39:9E:0F:B5:EA:C0:BD:FC:F5:27:3E:38:9B:BA:4E:AF:8B:35:AF:89
a=setup:actpass
a=mid:video
a=extmap:2 urn:ietf:params:rtp-hdrext:toffset
a=extmap:3 http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time
a=extmap:4 urn:3gpp:video-orientation
a=extmap:5 http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01
a=extmap:6 http://www.webrtc.org/experiments/rtp-hdrext/playout-delay
a=extmap:7 http://www.webrtc.org/experiments/rtp-hdrext/video-content-type
a=extmap:8 http://www.webrtc.org/experiments/rtp-hdrext/video-timing
a=extmap:10 http://tools.ietf.org/html/draft-ietf-avtext-framemarking-07
a=sendrecv
a=rtcp-mux
a=rtcp-rsize
a=rtpmap:96 H264/90000
a=rtcp-fb:96 goog-remb
a=rtcp-fb:96 transport-cc
a=rtcp-fb:96 ccm fir
a=rtcp-fb:96 nack
a=rtcp-fb:96 nack pli
a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=640c1f
a=rtpmap:97 rtx/90000
a=fmtp:97 apt=96
a=rtpmap:98 H264/90000
a=rtcp-fb:98 goog-remb
a=rtcp-fb:98 transport-cc
a=rtcp-fb:98 ccm fir
a=rtcp-fb:98 nack
a=rtcp-fb:98 nack pli
a=fmtp:98 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f
a=rtpmap:99 rtx/90000
a=fmtp:99 apt=98
a=rtpmap:100 VP8/90000
a=rtcp-fb:100 goog-remb
a=rtcp-fb:100 transport-cc
a=rtcp-fb:100 ccm fir
a=rtcp-fb:100 nack
a=rtcp-fb:100 nack pli
a=rtpmap:101 rtx/90000
a=fmtp:101 apt=100
a=rtpmap:127 red/90000
a=rtpmap:125 rtx/90000
a=fmtp:125 apt=127
a=rtpmap:104 ulpfec/90000
a=ssrc-group:FID 11942296 149700150
a=ssrc:11942296 cname:JTNiIZ6eJ7ghkHaB
a=ssrc:11942296 msid:cb7e185b-6110-4f65-b027-ddb8b5fa78c7 bd201f69-1364-40da-828f-cc695ff54a37
a=ssrc:11942296 mslabel:cb7e185b-6110-4f65-b027-ddb8b5fa78c7
a=ssrc:11942296 label:bd201f69-1364-40da-828f-cc695ff54a37
a=ssrc:149700150 cname:JTNiIZ6eJ7ghkHaB
a=ssrc:149700150 msid:cb7e185b-6110-4f65-b027-ddb8b5fa78c7 bd201f69-1364-40da-828f-cc695ff54a37
a=ssrc:149700150 mslabel:cb7e185b-6110-4f65-b027-ddb8b5fa78c7
a=ssrc:149700150 label:bd201f69-1364-40da-828f-cc695ff54a37
m=application 60277 DTLS/SCTP 5000
c=IN IP4 1.2.3.4
a=candidate:3317362580 1 udp 2113937151 192.168.0.87 60277 typ host generation 0 network-cost 999
a=candidate:3103151263 1 udp 2113939711 2a01:e0a:151:dc10:a8cb:5e93:9627:557c 60278 typ host generation 0 network-cost 999
a=candidate:842163049 1 udp 1677729535 82.64.133.208 60277 typ srflx raddr 192.168.0.87 rport 60277 generation 0 network-cost 999
a=ice-ufrag:XSmV
a=ice-pwd:Ss5xY4RMFEJASRvK5TIPgLN9
a=ice-options:trickle
a=fingerprint:sha-256 F2:68:A5:17:E7:85:D6:4E:23:F1:5D:02:39:9E:0F:B5:EA:C0:BD:FC:F5:27:3E:38:9B:BA:4E:AF:8B:35:AF:89
a=setup:actpass
a=mid:data
a=sctpmap:5000 webrtc-datachannel 1024
"""
            )
        )

        self.assertEqual(
            d.group,
            [GroupDescription(semantic="BUNDLE", items=["audio", "video", "data"])],
        )
        self.assertEqual(
            d.msid_semantic,
            [
                GroupDescription(
                    semantic="WMS", items=["cb7e185b-6110-4f65-b027-ddb8b5fa78c7"]
                )
            ],
        )
        self.assertEqual(d.host, None)
        self.assertEqual(d.name, "-")
        self.assertEqual(d.origin, "- 8148572839875102105 2 IN IP4 127.0.0.1")
        self.assertEqual(d.time, "0 0")
        self.assertEqual(d.version, 0)

        self.assertEqual(len(d.media), 3)
        self.assertEqual(d.media[0].kind, "audio")
        self.assertEqual(d.media[0].host, "1.2.3.4")
        self.assertEqual(d.media[0].port, 61015)
        self.assertEqual(d.media[0].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[0].direction, "sendrecv")
        self.assertEqual(d.media[0].msid, None)
        self.assertEqual(d.webrtc_track_id(d.media[0]), None)

        self.assertEqual(d.media[1].kind, "video")
        self.assertEqual(d.media[1].host, "1.2.3.4")
        self.assertEqual(d.media[1].port, 51044)
        self.assertEqual(d.media[1].profile, "UDP/TLS/RTP/SAVPF")
        self.assertEqual(d.media[1].direction, "sendrecv")
        self.assertEqual(d.media[1].msid, None)
        self.assertEqual(d.webrtc_track_id(d.media[0]), None)

        self.assertEqual(d.media[2].kind, "application")
        self.assertEqual(d.media[2].host, "1.2.3.4")
        self.assertEqual(d.media[2].port, 60277)
        self.assertEqual(d.media[2].profile, "DTLS/SCTP")
        self.assertEqual(d.media[2].direction, None)
        self.assertEqual(d.media[2].msid, None)


class H264SdpTest(TestCase):
    def assertParseFails(self, v: Any, msg: str) -> None:
        with self.assertRaises(ValueError) as cm:
            parse_h264_profile_level_id(v)
        self.assertEqual(str(cm.exception), msg)

    def test_parse_invalid(self) -> None:
        # invalid hexadecimal
        self.assertParseFails(None, "Expected a 6 character hexadecimal string")
        self.assertParseFails("", "Expected a 6 character hexadecimal string")
        self.assertParseFails("xyzxyz", "Expected a 6 character hexadecimal string")

        # invalid level
        self.assertParseFails("42E000", "0 is not a valid H264Level")
        self.assertParseFails("42E00F", "15 is not a valid H264Level")
        self.assertParseFails("42E0FF", "255 is not a valid H264Level")

        # invalid profile
        self.assertParseFails(
            "42E11F", "Unrecognized profile_iop = 225, profile_idc = 66"
        )
        self.assertParseFails(
            "58601F", "Unrecognized profile_iop = 96, profile_idc = 88"
        )
        self.assertParseFails(
            "64E01F", "Unrecognized profile_iop = 224, profile_idc = 100"
        )

    def test_parse_constrained_baseline(self) -> None:
        self.assertEqual(
            parse_h264_profile_level_id("42E01F"),
            (H264Profile.PROFILE_CONSTRAINED_BASELINE, H264Level.LEVEL3_1),
        )
        self.assertEqual(
            parse_h264_profile_level_id("42E00B"),
            (H264Profile.PROFILE_CONSTRAINED_BASELINE, H264Level.LEVEL1_1),
        )
        self.assertEqual(
            parse_h264_profile_level_id("42F00B"),
            (H264Profile.PROFILE_CONSTRAINED_BASELINE, H264Level.LEVEL1_B),
        )
        self.assertEqual(
            parse_h264_profile_level_id("42C02A"),
            (H264Profile.PROFILE_CONSTRAINED_BASELINE, H264Level.LEVEL4_2),
        )
        self.assertEqual(
            parse_h264_profile_level_id("58F01F"),
            (H264Profile.PROFILE_CONSTRAINED_BASELINE, H264Level.LEVEL3_1),
        )

    def test_parse_baseline(self) -> None:
        self.assertEqual(
            parse_h264_profile_level_id("42001F"),
            (H264Profile.PROFILE_BASELINE, H264Level.LEVEL3_1),
        )
        self.assertEqual(
            parse_h264_profile_level_id("42A01F"),
            (H264Profile.PROFILE_BASELINE, H264Level.LEVEL3_1),
        )
        self.assertEqual(
            parse_h264_profile_level_id("58A01F"),
            (H264Profile.PROFILE_BASELINE, H264Level.LEVEL3_1),
        )

    def test_parse_main(self) -> None:
        self.assertEqual(
            parse_h264_profile_level_id("4D401F"),
            (H264Profile.PROFILE_MAIN, H264Level.LEVEL3_1),
        )

    def test_parse_high(self) -> None:
        self.assertEqual(
            parse_h264_profile_level_id("64001F"),
            (H264Profile.PROFILE_HIGH, H264Level.LEVEL3_1),
        )

    def test_parse_constrained_high(self) -> None:
        self.assertEqual(
            parse_h264_profile_level_id("640C1F"),
            (H264Profile.PROFILE_CONSTRAINED_HIGH, H264Level.LEVEL3_1),
        )
