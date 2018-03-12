from unittest import TestCase

from aiortc.rtcrtpparameters import RTCRtpCodecParameters
from aiortc.sdp import SessionDescription


def lf2crlf(x):
    return x.replace('\n', '\r\n')


class SdpTest(TestCase):
    def test_audio_chrome(self):
        d = SessionDescription.parse(lf2crlf("""v=0
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
a=ssrc:1944796561 label:ec1eb8de-8df8-4956-ae81-879e5d062d12"""))  # noqa
        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, 'audio')
        self.assertEqual(d.media[0].host, '192.168.99.58')
        self.assertEqual(d.media[0].port, 45076)
        self.assertEqual(d.media[0].profile, 'UDP/TLS/RTP/SAVPF')
        self.assertEqual(d.media[0].direction, 'sendrecv')
        self.assertEqual(d.media[0].rtcp_host, '0.0.0.0')
        self.assertEqual(d.media[0].rtcp_port, 9)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # formats
        self.assertEqual(d.media[0].fmt, [
            111, 103, 104, 9, 0, 8, 106, 105, 13, 110, 112, 113, 126])
        self.assertEqual(d.media[0].rtp.codecs, [
            RTCRtpCodecParameters(name='opus', clockRate=48000, channels=2, payloadType=111),
            RTCRtpCodecParameters(name='ISAC', clockRate=16000, payloadType=103),
            RTCRtpCodecParameters(name='ISAC', clockRate=32000, payloadType=104),
            RTCRtpCodecParameters(name='G722', clockRate=8000, payloadType=9),
            RTCRtpCodecParameters(name='PCMU', clockRate=8000, payloadType=0),
            RTCRtpCodecParameters(name='PCMA', clockRate=8000, payloadType=8),
            RTCRtpCodecParameters(name='CN', clockRate=32000, payloadType=106),
            RTCRtpCodecParameters(name='CN', clockRate=16000, payloadType=105),
            RTCRtpCodecParameters(name='CN', clockRate=8000, payloadType=13),
            RTCRtpCodecParameters(name='telephone-event', clockRate=48000, payloadType=110),
            RTCRtpCodecParameters(name='telephone-event', clockRate=32000, payloadType=112),
            RTCRtpCodecParameters(name='telephone-event', clockRate=16000, payloadType=113),
            RTCRtpCodecParameters(name='telephone-event', clockRate=8000, payloadType=126),
        ])
        self.assertEqual(d.media[0].sctpmap, {})

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice.usernameFragment, '5+Ix')
        self.assertEqual(d.media[0].ice.password, 'uK8IlylxzDMUhrkVzdmj0M+v')

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, 'sha-256')
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            '6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC')  # noqa
        self.assertEqual(d.media[0].dtls.role, 'auto')

        self.assertEqual(str(d.media[0]), lf2crlf("""m=audio 45076 UDP/TLS/RTP/SAVPF 111 103 104 9 0 8 106 105 13 110 112 113 126
c=IN IP4 192.168.99.58
a=sendrecv
a=rtcp:9 IN IP4 0.0.0.0
a=rtcp-mux
a=rtpmap:111 opus/48000/2
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
a=candidate:2665802302 1 udp 2122262783 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 38475 typ host generation 0
a=candidate:1039001212 1 udp 2122194687 192.168.99.58 45076 typ host generation 0
a=candidate:3496416974 1 tcp 1518283007 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active generation 0
a=candidate:1936595596 1 tcp 1518214911 192.168.99.58 9 typ host tcptype active generation 0
a=ice-ufrag:5+Ix
a=ice-pwd:uK8IlylxzDMUhrkVzdmj0M+v
a=fingerprint:sha-256 6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC
a=setup:actpass
"""))  # noqa

    def test_audio_firefox(self):
        d = SessionDescription.parse(lf2crlf("""v=0
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
a=candidate:1 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 47387 typ host
a=candidate:2 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:3 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=candidate:0 2 UDP 2122187006 192.168.99.58 38612 typ host
a=candidate:1 2 UDP 2122252542 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 54301 typ host
a=candidate:2 2 TCP 2105458942 192.168.99.58 9 typ host tcptype active
a=candidate:3 2 TCP 2105524478 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=sendrecv
a=end-of-candidates
a=extmap:1/sendonly urn:ietf:params:rtp-hdrext:ssrc-audio-level
a=extmap:2 urn:ietf:params:rtp-hdrext:sdes:mid
a=fmtp:109 maxplaybackrate=48000;stereo=1;useinbandfec=1
a=fmtp:101 0-15
a=ice-pwd:f9b83487285016f7492197a5790ceee5
a=ice-ufrag:403a81e1
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
"""))  # noqa
        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, 'audio')
        self.assertEqual(d.media[0].host, '192.168.99.58')
        self.assertEqual(d.media[0].port, 45274)
        self.assertEqual(d.media[0].profile, 'UDP/TLS/RTP/SAVPF')
        self.assertEqual(d.media[0].direction, 'sendrecv')
        self.assertEqual(d.media[0].rtcp_host, '192.168.99.58')
        self.assertEqual(d.media[0].rtcp_port, 38612)
        self.assertEqual(d.media[0].rtcp_mux, True)

        # formats
        self.assertEqual(d.media[0].fmt, [
            109, 9, 0, 8, 101])
        self.assertEqual(d.media[0].rtp.codecs, [
            RTCRtpCodecParameters(name='opus', clockRate=48000, channels=2, payloadType=109),
            RTCRtpCodecParameters(name='G722', clockRate=8000, channels=1, payloadType=9),
            RTCRtpCodecParameters(name='PCMU', clockRate=8000, payloadType=0),
            RTCRtpCodecParameters(name='PCMA', clockRate=8000, payloadType=8),
            RTCRtpCodecParameters(name='telephone-event', clockRate=8000, payloadType=101),
        ])
        self.assertEqual(d.media[0].sctpmap, {})

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 8)
        self.assertEqual(d.media[0].ice.usernameFragment, '403a81e1')
        self.assertEqual(d.media[0].ice.password, 'f9b83487285016f7492197a5790ceee5')

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, 'sha-256')
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            'EB:A9:3E:50:D7:E3:B3:86:0F:7B:01:C1:EB:D6:AF:E4:97:DE:15:05:A8:DE:7B:83:56:C7:4B:6E:9D:75:D4:17')  # noqa
        self.assertEqual(d.media[0].dtls.role, 'auto')

    def test_datachannel_firefox(self):
        d = SessionDescription.parse(lf2crlf("""v=0
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
"""))  # noqa
        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].kind, 'application')
        self.assertEqual(d.media[0].host, '192.168.99.58')
        self.assertEqual(d.media[0].port, 45791)
        self.assertEqual(d.media[0].profile, 'DTLS/SCTP')
        self.assertEqual(d.media[0].fmt, [5000])
        self.assertEqual(d.media[0].sctpmap, {
            5000: 'webrtc-datachannel 256',
        })
        self.assertIsNotNone(d.media[0].sctpCapabilities)
        self.assertEqual(d.media[0].sctpCapabilities.maxMessageSize, 1073741823)

        # ice
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice.usernameFragment, '9889e0c4')
        self.assertEqual(d.media[0].ice.password, 'd30a5aec4dd81f07d4ff3344209400ab')

        # dtls
        self.assertEqual(len(d.media[0].dtls.fingerprints), 1)
        self.assertEqual(d.media[0].dtls.fingerprints[0].algorithm, 'sha-256')
        self.assertEqual(
            d.media[0].dtls.fingerprints[0].value,
            '39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74')  # noqa
        self.assertEqual(d.media[0].dtls.role, 'auto')

        self.assertEqual(str(d.media[0]), lf2crlf("""m=application 45791 DTLS/SCTP 5000
c=IN IP4 192.168.99.58
a=sendrecv
a=sctpmap:5000 webrtc-datachannel 256
a=max-message-size:1073741823
a=candidate:0 1 UDP 2122187007 192.168.99.58 45791 typ host
a=candidate:1 1 UDP 2122252543 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 44087 typ host
a=candidate:2 1 TCP 2105458943 192.168.99.58 9 typ host tcptype active
a=candidate:3 1 TCP 2105524479 2a02:a03f:3eb0:e000:b0aa:d60a:cff2:933c 9 typ host tcptype active
a=ice-ufrag:9889e0c4
a=ice-pwd:d30a5aec4dd81f07d4ff3344209400ab
a=fingerprint:sha-256 39:4A:09:1E:0E:33:32:85:51:03:49:95:54:0B:41:09:A2:10:60:CC:39:8F:C0:C4:45:FC:37:3A:55:EA:11:74
a=setup:actpass
"""))  # noqa
