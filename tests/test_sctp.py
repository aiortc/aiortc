import asyncio
import logging
import os
from unittest import TestCase

from aiowebrtc import sctp

from .utils import run


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()


def dummy_transport_pair():
    queue_a = asyncio.Queue()
    queue_b = asyncio.Queue()
    return (
        DummyTransport(rx_queue=queue_a, tx_queue=queue_b),
        DummyTransport(rx_queue=queue_b, tx_queue=queue_a),
    )


class DummyTransport:
    def __init__(self, rx_queue, tx_queue):
        self.rx_queue = rx_queue
        self.tx_queue = tx_queue

    async def recv(self):
        return await self.rx_queue.get()

    async def send(self, data):
        await self.tx_queue.put(data)


class SctpPacketTest(TestCase):
    def test_parse_init(self):
        data = load('sctp_init.bin')
        packet = sctp.Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 0)

        self.assertEqual(len(packet.chunks), 1)
        self.assertEqual(packet.chunks[0].type, sctp.ChunkType.INIT)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(len(packet.chunks[0].body), 82)

        self.assertEqual(bytes(packet), data)

    def test_parse_cookie_echo(self):
        data = load('sctp_cookie_echo.bin')
        packet = sctp.Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 1039286925)

        self.assertEqual(len(packet.chunks), 1)
        self.assertEqual(packet.chunks[0].type, sctp.ChunkType.COOKIE_ECHO)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(len(packet.chunks[0].body), 8)

        self.assertEqual(bytes(packet), data)


class SctpAssociationTest(TestCase):
    def test_server(self):
        client_transport, server_transport = dummy_transport_pair()
        client = sctp.Transport(is_server=False, transport=client_transport)
        server = sctp.Transport(is_server=True, transport=server_transport)
        asyncio.ensure_future(server.run())
        asyncio.ensure_future(client.run())
        run(asyncio.sleep(0.5))

        # DATA_CHANNEL_OPEN
        run(client.send(50, b'\x03\x00\x00\x00\x00\x00\x00\x00\x00\x04\x00\x00chat'))
        protocol, data = run(server.recv())
        self.assertEqual(protocol, 50)
        self.assertEqual(data, b'\x03\x00\x00\x00\x00\x00\x00\x00\x00\x04\x00\x00chat')

        run(asyncio.sleep(0.5))


logging.basicConfig(level=logging.DEBUG)
