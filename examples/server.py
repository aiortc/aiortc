import argparse
import asyncio
import logging
import re

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from aioquic.asyncio import serve

try:
    import uvloop
except ImportError:
    uvloop = None


TEMPLATE = """<!DOCTYPE html>
<html>
    <head>
        <meta charset="utf-8"/>
        <title>aioquic</title>
    </head>
    <body>
        <h1>Welcome to aioquic</h1>
        <p>{content}</p>
    </body>
</html>
"""


def render(content):
    return TEMPLATE.format(content=content).encode("utf8")


async def serve_http_request(reader, writer):
    """
    Serve an HTTP/0.9 request.
    """
    try:
        line = await reader.readline()
        method, path = line.decode("utf8").split()
    except (UnicodeDecodeError, ValueError):
        writer.write(render("Bad request"))
        writer.write_eof()
        return

    size_match = re.match(r"^/(\d+)$", path)
    if size_match:
        # we accept a maximum of 50MB
        size = min(50000000, int(size_match.group(1)))
        writer.write(b"Z" * size)
    elif path in ["/", "/index.html"]:
        writer.write(render("It works!"))
    else:
        writer.write(render("The document could not be found."))

    writer.write_eof()


def handle_stream(reader, writer):
    stream_id = writer.get_extra_info("stream_id")

    # we serve HTTP/0.9 on Client-Initiated Bidirectional streams
    if not stream_id % 4:
        asyncio.ensure_future(serve_http_request(reader, writer))


class SessionTicketStore:
    def __init__(self):
        self.tickets = {}

    def add(self, ticket):
        self.tickets[ticket.ticket] = ticket

    def pop(self, label):
        return self.tickets.pop(label, None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC server")
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

    with open(args.certificate, "rb") as fp:
        certificate = x509.load_pem_x509_certificate(
            fp.read(), backend=default_backend()
        )
    with open(args.private_key, "rb") as fp:
        private_key = serialization.load_pem_private_key(
            fp.read(), password=None, backend=default_backend()
        )

    if args.secrets_log:
        secrets_log_file = open(args.secrets_log, "a")
    else:
        secrets_log_file = None

    # session tickets
    ticket_store = SessionTicketStore()

    if uvloop is not None:
        uvloop.install()
    loop = asyncio.get_event_loop()
    protocol = loop.run_until_complete(
        serve(
            host=args.host,
            port=args.port,
            alpn_protocols=["hq-20"],
            certificate=certificate,
            private_key=private_key,
            stream_handler=handle_stream,
            secrets_log_file=secrets_log_file,
            session_ticket_fetcher=ticket_store.pop,
            session_ticket_handler=ticket_store.add,
            stateless_retry=args.stateless_retry,
        )
    )
    loop.run_forever()
