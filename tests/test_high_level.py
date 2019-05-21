import asyncio
from unittest import TestCase

from aioquic.client import connect
from aioquic.server import serve

from .utils import SERVER_CERTIFICATE, SERVER_PRIVATE_KEY, run


async def run_client(host, **kwargs):
    async with connect(host, 4433, **kwargs) as client:
        reader, writer = await client.create_stream()

        writer.write(b"ping")
        writer.write_eof()

        return await reader.read()


def handle_stream(reader, writer):
    async def serve():
        data = await reader.read()
        writer.write(bytes(reversed(data)))
        writer.write_eof()

    asyncio.ensure_future(serve())


async def run_server(stateless_retry):
    await serve(
        host="::",
        port="4433",
        certificate=SERVER_CERTIFICATE,
        private_key=SERVER_PRIVATE_KEY,
        stateless_retry=stateless_retry,
        stream_handler=handle_stream,
    )


class HighLevelTest(TestCase):
    def test_connect_and_serve(self):
        _, response = run(
            asyncio.gather(run_server(stateless_retry=False), run_client("127.0.0.1"))
        )
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_sni(self):
        _, response = run(
            asyncio.gather(run_server(stateless_retry=False), run_client("localhost"))
        )
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_stateless_retry(self):
        _, response = run(
            asyncio.gather(run_server(stateless_retry=True), run_client("127.0.0.1"))
        )
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_version_negotiation(self):
        _, response = run(
            asyncio.gather(
                run_server(stateless_retry=False),
                run_client("127.0.0.1", protocol_version=0x1A2A3A4A),
            )
        )
        self.assertEqual(response, b"gnip")

    def test_key_update(self):
        async def run_client_key_update(host, **kwargs):
            async with connect(host, 4433, **kwargs) as client:
                await client.ping()
                client.request_key_update()
                await client.ping()

        run(
            asyncio.gather(
                run_server(stateless_retry=False), run_client_key_update("127.0.0.1")
            )
        )

    def test_ping(self):
        async def run_client_ping(host, **kwargs):
            async with connect(host, 4433, **kwargs) as client:
                await client.ping()
                await client.ping()

        run(
            asyncio.gather(
                run_server(stateless_retry=False), run_client_ping("127.0.0.1")
            )
        )
