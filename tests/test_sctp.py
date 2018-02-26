import os
from unittest import TestCase

from aiowebrtc import sctp


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()


class SctpPacketTest(TestCase):
    def test_parse(self):
        data = load('sctp_init.bin')
        packet = sctp.Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 0)

        self.assertEqual(len(packet.chunks), 1)
        self.assertEqual(packet.chunks[0].type, sctp.ChunkType.INIT)
        self.assertEqual(packet.chunks[0].flags, 0)
        print(packet.chunks[0].params)
        self.assertEqual(len(packet.chunks[0].body), 82)

        self.assertEqual(bytes(packet), data)
