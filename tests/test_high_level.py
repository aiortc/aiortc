import asyncio
from unittest import TestCase

from aioquic.client import connect
from aioquic.server import serve

from .utils import SERVER_CERTIFICATE, SERVER_PRIVATE_KEY, run


async def run_client(host):
    async with connect(host, 4433) as client:
        reader, writer = await client.create_stream()

        writer.write(b"ping")
        writer.write_eof()

        return await reader.read()


async def handle_connection(connection):
    connection.stream_created_cb = handle_stream


def handle_stream(reader, writer):
    async def serve():
        data = await reader.read()
        writer.write(bytes(reversed(data)))
        writer.write_eof()

    asyncio.ensure_future(serve())


async def run_server(stateless_retry):
    await serve(
        handle_connection,
        host="::",
        port="4433",
        certificate=SERVER_CERTIFICATE,
        private_key=SERVER_PRIVATE_KEY,
        stateless_retry=stateless_retry,
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
