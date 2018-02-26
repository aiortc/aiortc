from unittest import TestCase

from aiowebrtc.sdp import ParsedDescription


class SdpTest(TestCase):
    def test_chrome_audio(self):
        d = ParsedDescription("""v=0
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
a=ssrc:1944796561 label:ec1eb8de-8df8-4956-ae81-879e5d062d12""")  # noqa
        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].type, 'audio')
        self.assertEqual(d.media[0].port, 45076)
        self.assertEqual(len(d.media[0].ice_candidates), 4)
        self.assertEqual(d.media[0].ice_ufrag, '5+Ix')
        self.assertEqual(d.media[0].ice_pwd, 'uK8IlylxzDMUhrkVzdmj0M+v')
        self.assertEqual(
            d.media[0].dtls_fingerprint,
            '6B:8B:5D:EA:59:04:20:23:29:C8:87:1C:CC:87:32:BE:DD:8C:66:A5:8E:50:55:EA:8C:D3:B6:5C:09:5E:D6:BC')  # noqa

    def test_firefox_audio(self):
        d = ParsedDescription("""v=0
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
""")  # noqa
        self.assertEqual(len(d.media), 1)
        self.assertEqual(d.media[0].type, 'audio')
        self.assertEqual(d.media[0].port, 45274)
        self.assertEqual(len(d.media[0].ice_candidates), 8)
        self.assertEqual(d.media[0].ice_ufrag, '403a81e1')
        self.assertEqual(d.media[0].ice_pwd, 'f9b83487285016f7492197a5790ceee5')
        self.assertEqual(
            d.media[0].dtls_fingerprint,
            'EB:A9:3E:50:D7:E3:B3:86:0F:7B:01:C1:EB:D6:AF:E4:97:DE:15:05:A8:DE:7B:83:56:C7:4B:6E:9D:75:D4:17')  # noqa
