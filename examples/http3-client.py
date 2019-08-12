import argparse
import asyncio
import json
import logging
import pickle
import sys
import time
from collections import deque
from typing import Deque, Dict, Optional, Union, cast
from urllib.parse import urlparse

from aioquic.asyncio.client import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h0.connection import H0Connection
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, HttpEvent, ResponseReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent
from aioquic.quic.logger import QuicLogger

try:
    import uvloop
except ImportError:
    uvloop = None

logger = logging.getLogger("client")

HttpConnection = Union[H0Connection, H3Connection]


class HttpClient(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._http: Optional[HttpConnection] = None
        self._request_events: Dict[int, Deque[HttpEvent]] = {}
        self._request_waiter: Dict[int, asyncio.Future[Deque[HttpEvent]]] = {}

        if configuration.alpn_protocols[0].startswith("hq-"):
            self._http = H0Connection(self._quic)
        else:
            self._http = H3Connection(self._quic)

    async def get(self, authority: str, path: str) -> Deque[HttpEvent]:
        """
        Perform a GET request.
        """
        stream_id = self._quic.get_next_available_stream_id()
        self._http.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", authority.encode("utf8")),
                (b":path", path.encode("utf8")),
            ],
        )
        self._http.send_data(stream_id=stream_id, data=b"", end_stream=True)

        waiter = self._loop.create_future()
        self._request_events[stream_id] = deque()
        self._request_waiter[stream_id] = waiter
        self._send_pending()

        return await asyncio.shield(waiter)

    def http_event_received(self, event: HttpEvent):
        if (
            isinstance(event, (ResponseReceived, DataReceived))
            and event.stream_id in self._request_events
        ):
            self._request_events[event.stream_id].append(event)
            if event.stream_ended:
                request_waiter = self._request_waiter.pop(event.stream_id)
                request_waiter.set_result(self._request_events.pop(event.stream_id))

    def quic_event_received(self, event: QuicEvent):
        #  pass event to the HTTP layer
        if self._http is not None:
            for http_event in self._http.handle_event(event):
                self.http_event_received(http_event)


def save_session_ticket(ticket):
    """
    Callback which is invoked by the TLS engine when a new session ticket
    is received.
    """
    logger.info("New session ticket received")
    if args.session_ticket:
        with open(args.session_ticket, "wb") as fp:
            pickle.dump(ticket, fp)


async def run(url: str, configuration: QuicConfiguration) -> None:
    # parse URL
    parsed = urlparse(url)
    assert parsed.scheme == "https", "Only HTTPS URLs are supported."
    if ":" in parsed.netloc:
        host, port_str = parsed.netloc.split(":")
        port = int(port_str)
    else:
        host = parsed.netloc
        port = 443

    async with connect(
        host,
        port,
        configuration=configuration,
        create_protocol=HttpClient,
        session_ticket_handler=save_session_ticket,
    ) as client:
        client = cast(HttpClient, client)

        # perform request
        start = time.time()
        http_events = await client.get(parsed.netloc, parsed.path)
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

    # prepare configuration
    configuration = QuicConfiguration(
        is_client=True, alpn_protocols=["hq-22" if args.legacy_http else "h3-22"]
    )
    if args.quic_log:
        configuration.quic_logger = QuicLogger()
    if args.secrets_log:
        configuration.secrets_log_file = open(args.secrets_log, "a")
    if args.session_ticket:
        try:
            with open(args.session_ticket, "rb") as fp:
                configuration.session_ticket = pickle.load(fp)
        except FileNotFoundError:
            pass

    if uvloop is not None:
        uvloop.install()
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run(url=args.url, configuration=configuration))
    finally:
        if configuration.quic_logger is not None:
            with open(args.quic_log, "w") as logger_fp:
                json.dump(configuration.quic_logger.to_dict(), logger_fp, indent=4)
