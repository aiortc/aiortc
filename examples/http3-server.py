import argparse
import asyncio
import importlib
import json
import logging
import os
import time
from email.utils import formatdate
from typing import Callable, Dict, Optional, Text, Union, cast

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

import aioquic.quic.events
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.buffer import Buffer
from aioquic.h0.connection import H0Connection
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, Event, RequestReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import NetworkAddress, QuicConnection
from aioquic.quic.events import QuicEvent
from aioquic.quic.logger import QuicLogger
from aioquic.quic.packet import (
    PACKET_TYPE_INITIAL,
    encode_quic_retry,
    encode_quic_version_negotiation,
    pull_quic_header,
)
from aioquic.quic.retry import QuicRetryTokenHandler
from aioquic.tls import SessionTicket, SessionTicketFetcher, SessionTicketHandler

try:
    import uvloop
except ImportError:
    uvloop = None

AsgiApplication = Callable
HttpConnection = Union[H0Connection, H3Connection]


class HttpRequestHandler:
    def __init__(
        self,
        *,
        connection: HttpConnection,
        scope: Dict,
        send_pending: Callable[[], None],
        stream_id: int,
    ):
        self.connection = connection
        self.queue: asyncio.Queue[Dict] = asyncio.Queue()
        self.scope = scope
        self.send_pending = send_pending
        self.stream_id = stream_id

    async def run_asgi(self, app: AsgiApplication) -> None:
        await application(self.scope, self.receive, self.send)

        self.connection.send_data(stream_id=self.stream_id, data=b"", end_stream=True)
        self.send_pending()

    async def receive(self) -> Dict:
        return await self.queue.get()

    async def send(self, message: Dict):
        if message["type"] == "http.response.start":
            self.connection.send_headers(
                stream_id=self.stream_id,
                headers=[
                    (b":status", str(message["status"]).encode("ascii")),
                    (b"server", b"aioquic"),
                    (b"date", formatdate(time.time(), usegmt=True).encode()),
                ]
                + [(k, v) for k, v in message["headers"]],
            )
        elif message["type"] == "http.response.body":
            self.connection.send_data(
                stream_id=self.stream_id, data=message["body"], end_stream=False
            )
        self.send_pending()


class HttpServer(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        application: AsgiApplication,
        configuration: QuicConfiguration,
        session_ticket_fetcher: Optional[SessionTicketFetcher] = None,
        session_ticket_handler: Optional[SessionTicketHandler] = None,
        stateless_retry: bool = False,
    ) -> None:
        self._application = application
        self._configuration = configuration
        self._loop = asyncio.get_event_loop()
        self._protocols: Dict[bytes, QuicConnectionProtocol] = {}
        self._session_ticket_fetcher = session_ticket_fetcher
        self._session_ticket_handler = session_ticket_handler
        self._transport: Optional[asyncio.DatagramTransport] = None

        if stateless_retry:
            self._retry = QuicRetryTokenHandler()
        else:
            self._retry = None

    def close(self):
        for protocol in set(self._protocols.values()):
            protocol.close()
        self._protocols.clear()
        self._transport.close()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = cast(asyncio.DatagramTransport, transport)

    def datagram_received(self, data: Union[bytes, Text], addr: NetworkAddress) -> None:
        data = cast(bytes, data)
        buf = Buffer(data=data)
        header = pull_quic_header(buf, host_cid_length=8)

        # version negotiation
        if (
            header.version is not None
            and header.version not in self._configuration.supported_versions
        ):
            self._transport.sendto(
                encode_quic_version_negotiation(
                    source_cid=header.destination_cid,
                    destination_cid=header.source_cid,
                    supported_versions=self._configuration.supported_versions,
                ),
                addr,
            )
            return

        protocol = self._protocols.get(header.destination_cid, None)
        original_connection_id: Optional[bytes] = None
        if protocol is None and header.packet_type == PACKET_TYPE_INITIAL:
            # stateless retry
            if self._retry is not None:
                if not header.token:
                    # create a retry token
                    self._transport.sendto(
                        encode_quic_retry(
                            version=header.version,
                            source_cid=os.urandom(8),
                            destination_cid=header.source_cid,
                            original_destination_cid=header.destination_cid,
                            retry_token=self._retry.create_token(
                                addr, header.destination_cid
                            ),
                        ),
                        addr,
                    )
                    return
                else:
                    # validate retry token
                    try:
                        original_connection_id = self._retry.validate_token(
                            addr, header.token
                        )
                    except ValueError:
                        return

            # create new connection
            connection = QuicConnection(
                configuration=self._configuration,
                original_connection_id=original_connection_id,
                session_ticket_fetcher=self._session_ticket_fetcher,
                session_ticket_handler=self._session_ticket_handler,
            )
            protocol = HttpServerProtocol(connection, self)
            protocol.connection_made(self._transport)

            self._protocols[header.destination_cid] = protocol
            self._protocols[connection.host_cid] = protocol

        if protocol is not None:
            protocol.datagram_received(data, addr)


class HttpServerProtocol(QuicConnectionProtocol):
    def __init__(self, quic: QuicConnection, server: HttpServer):
        super().__init__(quic)
        self._handlers: Dict[int, HttpRequestHandler] = {}
        self._http: Optional[HttpConnection] = None
        self._server = server

    def _handle_event(self, event: QuicEvent):
        if isinstance(event, aioquic.quic.events.ConnectionTerminated):
            # remove the connection
            for cid, protocol in list(self._server._protocols.items()):
                if protocol == self:
                    del self._server._protocols[cid]
            return
        elif isinstance(event, aioquic.quic.events.ProtocolNegotiated):
            if event.alpn_protocol == "h3-22":
                self._http = H3Connection(self._quic)
            elif event.alpn_protocol == "hq-22":
                self._http = H0Connection(self._quic)
        elif isinstance(event, aioquic.quic.events.ConnectionIdIssued):
            self._server._protocols[event.connection_id] = self
        elif isinstance(event, aioquic.quic.events.ConnectionIdRetired):
            assert self._server._protocols[event.connection_id] == self
            del self._server._protocols[event.connection_id]

        #  pass event to the HTTP layer
        if self._http is not None:
            for http_event in self._http.handle_event(event):
                self.handle_http_event(http_event)

    def handle_http_event(self, event: Event) -> None:
        if isinstance(event, RequestReceived):
            headers = []
            raw_path = b""
            method = ""
            for header, value in event.headers:
                if header == b":authority":
                    headers.append((b"host", value))
                elif header == b":method":
                    method = value.decode("utf8")
                elif header == b":path":
                    raw_path = value
                elif header and not header.startswith(b":"):
                    headers.append((header, value))

            if b"?" in raw_path:
                path_bytes, query_string = raw_path.split(b"?", maxsplit=1)
            else:
                path_bytes, query_string = raw_path, b""

            scope = {
                "headers": headers,
                "http_version": "0.9" if isinstance(self._http, H0Connection) else "3",
                "method": method,
                "path": path_bytes.decode("utf8"),
                "query_string": query_string,
                "raw_path": raw_path,
                "root_path": "",
                "scheme": "https",
                "type": "http",
            }

            handler = HttpRequestHandler(
                connection=self._http,
                scope=scope,
                send_pending=self._send_pending,
                stream_id=event.stream_id,
            )
            self._handlers[event.stream_id] = handler
            asyncio.ensure_future(handler.run_asgi(self._server._application))
        elif isinstance(event, DataReceived):
            handler = self._handlers[event.stream_id]
            handler.queue.put_nowait(
                {
                    "type": "http.request",
                    "body": event.data,
                    "more_body": not event.stream_ended,
                }
            )


class SessionTicketStore:
    """
    Simple in-memory store for session tickets.
    """

    def __init__(self) -> None:
        self.tickets: Dict[bytes, SessionTicket] = {}

    def add(self, ticket: SessionTicket) -> None:
        self.tickets[ticket.ticket] = ticket

    def pop(self, label: bytes) -> Optional[SessionTicket]:
        return self.tickets.pop(label, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC server")
    parser.add_argument(
        "app",
        type=str,
        nargs="?",
        default="demo:app",
        help="the ASGI application as <module>:<attribute>",
    )
    parser.add_argument(
        "-c",
        "--certificate",
        type=str,
        required=True,
        help="load the TLS certificate from the specified file",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="::",
        help="listen on the specified address (defaults to ::)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4433,
        help="listen on the specified port (defaults to 4433)",
    )
    parser.add_argument(
        "-k",
        "--private-key",
        type=str,
        required=True,
        help="load the TLS private key from the specified file",
    )
    parser.add_argument(
        "-l",
        "--secrets-log",
        type=str,
        help="log secrets to a file, for use with Wireshark",
    )
    parser.add_argument(
        "-q", "--quic-log", type=str, help="log QUIC events to a file in QLOG format"
    )
    parser.add_argument(
        "-r",
        "--stateless-retry",
        action="store_true",
        help="send a stateless retry for new connections",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="increase logging verbosity"
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    # import ASGI application
    module_str, attr_str = args.app.split(":", maxsplit=1)
    module = importlib.import_module(module_str)
    application = getattr(module, attr_str)

    # create QUIC logger
    if args.quic_log:
        quic_logger = QuicLogger()
    else:
        quic_logger = None

    # open SSL log file
    if args.secrets_log:
        secrets_log_file = open(args.secrets_log, "a")
    else:
        secrets_log_file = None

    # load SSL certificate and key
    with open(args.certificate, "rb") as fp:
        certificate = x509.load_pem_x509_certificate(
            fp.read(), backend=default_backend()
        )
    with open(args.private_key, "rb") as fp:
        private_key = serialization.load_pem_private_key(
            fp.read(), password=None, backend=default_backend()
        )

    configuration = QuicConfiguration(
        alpn_protocols=["h3-22", "hq-22"],
        certificate=certificate,
        is_client=False,
        private_key=private_key,
        quic_logger=quic_logger,
        secrets_log_file=secrets_log_file,
    )
    ticket_store = SessionTicketStore()

    if uvloop is not None:
        uvloop.install()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        loop.create_datagram_endpoint(
            lambda: HttpServer(
                application=application,
                configuration=configuration,
                session_ticket_fetcher=ticket_store.pop,
                session_ticket_handler=ticket_store.add,
                stateless_retry=args.stateless_retry,
            ),
            local_addr=(args.host, args.port),
        )
    )
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if quic_logger is not None:
            with open(args.quic_log, "w") as logger_fp:
                json.dump(quic_logger.to_dict(), logger_fp, indent=4)
