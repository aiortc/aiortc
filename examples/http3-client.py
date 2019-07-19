import argparse
import asyncio
import json
import logging
import pickle
import socket
import sys
import time
from typing import Dict, List, Optional, Text, Union, cast
from urllib.parse import urlparse

from aioquic.h0.connection import H0Connection
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, Event, ResponseReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import NetworkAddress, QuicConnection
from aioquic.quic.events import ConnectionTerminated
from aioquic.quic.logger import QuicLogger
from aioquic.tls import SessionTicketHandler

try:
    import uvloop
except ImportError:
    uvloop = None

logger = logging.getLogger("client")

HttpConnection = Union[H0Connection, H3Connection]


class HttpClient(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        configuration: QuicConfiguration,
        server_addr: NetworkAddress,
        session_ticket_handler: Optional[SessionTicketHandler] = None
    ):
        self._closed = asyncio.Event()
        self._connect_called = False
        self._http: HttpConnection
        self._loop = asyncio.get_event_loop()
        self._quic = QuicConnection(
            configuration=configuration, session_ticket_handler=session_ticket_handler
        )
        self._server_addr = server_addr
        self._timer: Optional[asyncio.TimerHandle] = None
        self._timer_at = 0.0

        self._request_events: Dict[int, List[Event]] = {}
        self._request_waiter: Dict[int, asyncio.Future[List[Event]]] = {}

        if configuration.alpn_protocols[0].startswith("hq-"):
            self._http = H0Connection(self._quic)
        else:
            self._http = H3Connection(self._quic)

    async def close(self) -> None:
        """
        Close the connection.
        """
        self._quic.close()
        self._consume_events()
        await self._closed.wait()

    async def get(self, path: str) -> List[Event]:
        """
        Perform a GET request.
        """
        if not self._connect_called:
            self._quic.connect(self._server_addr, now=self._loop.time())
            self._connect_called = True

        stream_id = self._quic.get_next_available_stream_id()
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", self._quic.configuration.server_name.encode("utf8")),
                (b":path", path.encode("utf8")),
            ],
        )
        self._http.send_data(stream_id=stream_id, data=b"", end_stream=True)

        waiter = self._loop.create_future()
        self._request_events[stream_id] = []
        self._request_waiter[stream_id] = waiter
        self._consume_events()

        return await asyncio.shield(waiter)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = cast(asyncio.DatagramTransport, transport)

    def datagram_received(self, data: Union[bytes, Text], addr: NetworkAddress) -> None:
        self._quic.receive_datagram(cast(bytes, data), addr, self._loop.time())
        self._consume_events()

    def _consume_events(self) -> None:
        # process events
        event = self._quic.next_event()
        while event is not None:
            #  pass event to the HTTP layer
            for http_event in self._http.handle_event(event):
                if (
                    isinstance(http_event, (ResponseReceived, DataReceived))
                    and http_event.stream_id in self._request_events
                ):
                    self._request_events[http_event.stream_id].append(http_event)
                    if http_event.stream_ended:
                        request_waiter = self._request_waiter.pop(http_event.stream_id)
                        request_waiter.set_result(
                            self._request_events.pop(http_event.stream_id)
                        )

            if isinstance(event, ConnectionTerminated):
                self._closed.set()

            event = self._quic.next_event()

        # send datagrams
        for data, addr in self._quic.datagrams_to_send(now=self._loop.time()):
            self._transport.sendto(data, addr)

        # re-arm timer
        timer_at = self._quic.get_timer()
        if self._timer is not None and self._timer_at != timer_at:
            self._timer.cancel()
            self._timer = None
        if self._timer is None and timer_at is not None:
            self._timer = self._loop.call_at(timer_at, self._handle_timer)
        self._timer_at = timer_at

    def _handle_timer(self) -> None:
        now = max(self._timer_at, self._loop.time())
        self._timer = None
        self._timer_at = None
        self._quic.handle_timer(now=now)
        self._consume_events()


def save_session_ticket(ticket):
    """
    Callback which is invoked by the TLS engine when a new session ticket
    is received.
    """
    logger.info("New session ticket received")
    if args.session_ticket:
        with open(args.session_ticket, "wb") as fp:
            pickle.dump(ticket, fp)


async def run(url: str, legacy_http: bool, **kwargs) -> None:
    # parse URL
    parsed = urlparse(url)
    assert parsed.scheme == "https", "Only HTTPS URLs are supported."
    if ":" in parsed.netloc:
        server_name, port_str = parsed.netloc.split(":")
        port = int(port_str)
    else:
        server_name = parsed.netloc
        port = 443

    # lookup remote address
    infos = await loop.getaddrinfo(server_name, port, type=socket.SOCK_DGRAM)
    server_addr = infos[0][4]
    if len(server_addr) == 2:
        server_addr = ("::ffff:" + server_addr[0], server_addr[1], 0, 0)

    # prepare QUIC connection
    _, client = await loop.create_datagram_endpoint(
        lambda: HttpClient(
            configuration=QuicConfiguration(
                alpn_protocols=["hq-22" if legacy_http else "h3-22"],
                is_client=True,
                server_name=server_name,
                **kwargs
            ),
            server_addr=server_addr,
            session_ticket_handler=save_session_ticket,
        ),
        local_addr=("::", 0),
    )
    client = cast(HttpClient, client)

    # perform request
    start = time.time()
    http_events = await client.get(parsed.path)
    elapsed = time.time() - start

    # print speed
    octets = 0
    for http_event in http_events:
        if isinstance(http_event, DataReceived):
            octets += len(http_event.data)
    logger.info(
        "Received %d bytes in %.1f s (%.3f Mbps)"
        % (octets, elapsed, octets * 8 / elapsed / 1000000)
    )

    # print response
    for http_event in http_events:
        if isinstance(http_event, ResponseReceived):
            headers = b""
            for k, v in http_event.headers:
                headers += k + b": " + v + b"\r\n"
            if headers:
                sys.stderr.buffer.write(headers + b"\r\n")
                sys.stderr.buffer.flush()
        elif isinstance(http_event, DataReceived):
            sys.stdout.buffer.write(http_event.data)
            sys.stdout.buffer.flush()

    # close QUIC connection
    await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP/3 client")
    parser.add_argument("url", type=str, help="the URL to query (must be HTTPS)")
    parser.add_argument("--legacy-http", action="store_true", help="use HTTP/0.9")
    parser.add_argument(
        "-q", "--quic-log", type=str, help="log QUIC events to a file in QLOG format"
    )
    parser.add_argument(
        "-l",
        "--secrets-log",
        type=str,
        help="log secrets to a file, for use with Wireshark",
    )
    parser.add_argument(
        "-s",
        "--session-ticket",
        type=str,
        help="read and write session ticket from the specified file",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="increase logging verbosity"
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

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

    # load session ticket
    session_ticket = None
    if args.session_ticket:
        try:
            with open(args.session_ticket, "rb") as fp:
                session_ticket = pickle.load(fp)
        except FileNotFoundError:
            pass

    if uvloop is not None:
        uvloop.install()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(
            run(
                url=args.url,
                legacy_http=args.legacy_http,
                quic_logger=quic_logger,
                secrets_log_file=secrets_log_file,
                session_ticket=session_ticket,
            )
        )
    finally:
        if quic_logger is not None:
            with open(args.quic_log, "w") as logger_fp:
                json.dump(quic_logger.to_dict(), logger_fp, indent=4)
