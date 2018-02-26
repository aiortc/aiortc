import os
from unittest import TestCase

from aiowebrtc import sctp


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()


class SctpPacketTest(TestCase):
    def test_parse(self):
        data = load('sctp.bin')
        packet = sctp.Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 0)
