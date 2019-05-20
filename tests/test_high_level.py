import asyncio
from unittest import TestCase

import aioquic

from .utils import SERVER_CERTIFICATE, SERVER_PRIVATE_KEY, run


async def run_client():
    async with aioquic.connect("127.0.0.1", 4433) as client:
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
    await aioquic.serve(
        handle_connection,
        host="127.0.0.1",
        port="4433",
        certificate=SERVER_CERTIFICATE,
        private_key=SERVER_PRIVATE_KEY,
        stateless_retry=stateless_retry,
    )


class HighLevelTest(TestCase):
    def test_connect_and_serve(self):
        _, response = run(
            asyncio.gather(run_server(stateless_retry=False), run_client())
        )
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_stateless_retry(self):
        _, response = run(
            asyncio.gather(run_server(stateless_retry=True), run_client())
        )
        self.assertEqual(response, b"gnip")
