from unittest import TestCase

from aiortc.utils import parse_stun_turn_uri


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
            'port': None,
            'scheme': 'stun',
        })

    def test_stun_with_port(self):
        uri = parse_stun_turn_uri('stun:stun.l.google.com:19302')
        self.assertEqual(uri, {
            'host': 'stun.l.google.com',
            'port': 19302,
            'scheme': 'stun',
        })

    def test_turn_with_port_and_transport(self):
        uri = parse_stun_turn_uri('turn:1.2.3.4:3478?transport=udp')
        self.assertEqual(uri, {
            'host': '1.2.3.4',
            'port': 3478,
            'scheme': 'turn',
            'transport': 'udp',
        })
