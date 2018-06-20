from unittest import TestCase

from aiortc.rtcconfiguration import RTCIceServer
from aiortc.rtcicetransport import (RTCIceGatherer, connection_kwargs,
                                    parse_stun_turn_uri)


class ConnectionKwargsTest(TestCase):
    def test_empty(self):
        self.assertEqual(connection_kwargs([]), {})

    def test_stun(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer('stun:stun.l.google.com:19302'),
        ]), {
            'stun_server': ('stun.l.google.com', 19302),
        })

    def test_stun_multiple_servers(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer('stun:stun.l.google.com:19302'),
            RTCIceServer('stun:stun.example.com'),
        ]), {
            'stun_server': ('stun.l.google.com', 19302),
        })

    def test_stun_multiple_urls(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer([
                'stun:stun1.l.google.com:19302',
                'stun:stun2.l.google.com:19302',
            ]),
        ]), {
            'stun_server': ('stun1.l.google.com', 19302),
        })

    def test_turn(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer('turn:turn.example.com'),
        ]), {
            'turn_password': None,
            'turn_server': ('turn.example.com', 3478),
            'turn_username': None,
        })

    def test_turn_multiple_servers(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer('turn:turn.example.com'),
            RTCIceServer('turn:turn.example.net'),
        ]), {
            'turn_password': None,
            'turn_server': ('turn.example.com', 3478),
            'turn_username': None,
        })

    def test_turn_multiple_urls(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer([
                'turn:turn1.example.com',
                'turn:turn2.example.com',
            ])
        ]), {
            'turn_password': None,
            'turn_server': ('turn1.example.com', 3478),
            'turn_username': None,
        })

    def test_turn_over_tcp(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer('turn:turn.example.com?transport=tcp'),
        ]), {})

    def test_turn_with_password(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer(
                urls='turn:turn.example.com',
                username='foo',
                credential='bar'
            ),
        ]), {
            'turn_password': 'bar',
            'turn_server': ('turn.example.com', 3478),
            'turn_username': 'foo',
        })

    def test_turn_with_token(self):
        self.assertEqual(connection_kwargs([
            RTCIceServer(
                urls='turn:turn.example.com',
                username='foo',
                credential='bar',
                credentialType='token',
            ),
        ]), {})


class ParseStunTurnUriTest(TestCase):
    def test_invalid_scheme(self):
        with self.assertRaises(ValueError) as cm:
            parse_stun_turn_uri('foo')
        self.assertEqual(str(cm.exception), 'malformed uri: invalid scheme')

    def test_invalid_uri(self):
        with self.assertRaises(ValueError) as cm:
            parse_stun_turn_uri('stun')
        self.assertEqual(str(cm.exception), 'malformed uri')

    def test_stun(self):
        uri = parse_stun_turn_uri('stun:stun.services.mozilla.com')
        self.assertEqual(uri, {
            'host': 'stun.services.mozilla.com',
            'port': 3478,
            'scheme': 'stun',
        })

    def test_stuns(self):
        uri = parse_stun_turn_uri('stuns:stun.services.mozilla.com')
        self.assertEqual(uri, {
            'host': 'stun.services.mozilla.com',
            'port': 5349,
            'scheme': 'stuns',
        })

    def test_stun_with_port(self):
        uri = parse_stun_turn_uri('stun:stun.l.google.com:19302')
        self.assertEqual(uri, {
            'host': 'stun.l.google.com',
            'port': 19302,
            'scheme': 'stun',
        })

    def test_turn(self):
        uri = parse_stun_turn_uri('turn:1.2.3.4')
        self.assertEqual(uri, {
            'host': '1.2.3.4',
            'port': 3478,
            'scheme': 'turn',
            'transport': 'udp',
        })

    def test_turn_with_port_and_transport(self):
        uri = parse_stun_turn_uri('turn:1.2.3.4:3478?transport=tcp')
        self.assertEqual(uri, {
            'host': '1.2.3.4',
            'port': 3478,
            'scheme': 'turn',
            'transport': 'tcp',
        })

    def test_turns(self):
        uri = parse_stun_turn_uri('turns:1.2.3.4')
        self.assertEqual(uri, {
            'host': '1.2.3.4',
            'port': 5349,
            'scheme': 'turns',
            'transport': 'tcp',
        })

    def test_turns_with_port_and_transport(self):
        uri = parse_stun_turn_uri('turns:1.2.3.4:1234?transport=tcp')
        self.assertEqual(uri, {
            'host': '1.2.3.4',
            'port': 1234,
            'scheme': 'turns',
            'transport': 'tcp',
        })


class RTCIceGathererTest(TestCase):
    def test_default_ice_servers(self):
        self.assertEqual(RTCIceGatherer.getDefaultIceServers(), [
            RTCIceServer(urls='stun:stun.l.google.com:19302')
        ])
