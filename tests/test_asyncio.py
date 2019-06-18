import asyncio
import random
import socket
from unittest import TestCase
from unittest.mock import patch

from aioquic.asyncio.client import connect
from aioquic.asyncio.server import serve

from .utils import SERVER_CERTIFICATE, SERVER_PRIVATE_KEY, run

real_sendto = socket.socket.sendto


def sendto_with_loss(self, data, addr=None):
    """
    Simulate 25% packet loss.
    """
    if random.random() > 0.25:
        real_sendto(self, data, addr)


class SessionTicketStore:
    def __init__(self):
        self.tickets = {}

    def add(self, ticket):
        self.tickets[ticket.ticket] = ticket

    def pop(self, label):
        return self.tickets.pop(label, None)


async def run_client(host, port=4433, request=b"ping", **kwargs):
    async with connect(host, port, **kwargs) as client:
        reader, writer = await client.create_stream()
        assert writer.can_write_eof() is True
        assert writer.get_extra_info("stream_id") == 0

        writer.write(request)
        writer.write_eof()

        return await reader.read()


def handle_stream(reader, writer):
    async def serve():
        data = await reader.read()
        writer.write(bytes(reversed(data)))
        writer.write_eof()

    asyncio.ensure_future(serve())


async def run_server(**kwargs):
    await serve(
        host="::",
        port="4433",
        certificate=SERVER_CERTIFICATE,
        private_key=SERVER_PRIVATE_KEY,
        stream_handler=handle_stream,
        **kwargs
    )


class HighLevelTest(TestCase):
    def test_connect_and_serve(self):
        _, response = run(asyncio.gather(run_server(), run_client("127.0.0.1")))
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_large(self):
        """
        Transfer enough data to require raising MAX_DATA and MAX_STREAM_DATA.
        """
        data = b"Z" * 2097152
        _, response = run(
            asyncio.gather(run_server(), run_client("127.0.0.1", request=data))
        )
        self.assertEqual(response, data)

    def test_connect_and_serve_writelines(self):
        async def run_client_writelines(host, port=4433, **kwargs):
            async with connect(host, port, **kwargs) as client:
                reader, writer = await client.create_stream()
                assert writer.can_write_eof() is True

                writer.writelines([b"01234567", b"89012345"])
                writer.write_eof()

                return await reader.read()

        _, response = run(
            asyncio.gather(run_server(), run_client_writelines("127.0.0.1"))
        )
        self.assertEqual(response, b"5432109876543210")

    @patch("socket.socket.sendto", new_callable=lambda: sendto_with_loss)
    def test_connect_and_serve_with_packet_loss(self, mock_sendto):
        """
        This test ensures handshake success and stream data is successfully sent
        and received in the presence of packet loss (randomized 25% in each direction).
        """
        data = b"Z" * 65536
        _, response = run(
            asyncio.gather(
                run_server(stateless_retry=True), run_client("127.0.0.1", request=data)
            )
        )
        self.assertEqual(response, data)

    def test_connect_and_serve_with_session_ticket(self):
        client_ticket = None
        store = SessionTicketStore()

        def save_ticket(t):
            nonlocal client_ticket
            client_ticket = t

        # first request
        _, response = run(
            asyncio.gather(
                run_server(session_ticket_handler=store.add),
                run_client("127.0.0.1", session_ticket_handler=save_ticket),
            )
        )
        self.assertEqual(response, b"gnip")

        self.assertIsNotNone(client_ticket)

        # second request
        _, response = run(
            asyncio.gather(
                run_server(session_ticket_fetcher=store.pop),
                run_client("127.0.0.1", session_ticket=client_ticket),
            )
        )
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_sni(self):
        _, response = run(asyncio.gather(run_server(), run_client("localhost")))
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_stateless_retry(self):
        _, response = run(
            asyncio.gather(run_server(stateless_retry=True), run_client("127.0.0.1"))
        )
        self.assertEqual(response, b"gnip")

    def test_connect_and_serve_with_version_negotiation(self):
        _, response = run(
            asyncio.gather(
                run_server(), run_client("127.0.0.1", protocol_version=0x1A2A3A4A)
            )
        )
        self.assertEqual(response, b"gnip")

    def test_connect_timeout(self):
        with self.assertRaises(ConnectionError):
            run(run_client("127.0.0.1", port=4400, idle_timeout=5))

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
