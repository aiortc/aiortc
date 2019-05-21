import argparse
import asyncio
import binascii
import logging
import re

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

import aioquic

logger = logging.getLogger("server")

TEMPLATE = """<!DOCTYPE html>
<html>
    <head>
        <meta charset="utf-8"/>
        <title>aioquic</title>
    </head>
    <body>
        <h1>Welcome to aioquic</h1>
        <p>{content}/p>
    </body>
</html>
"""


def connection_id(connection):
    return "Connection %s" % binascii.hexlify(connection.host_cid).decode("ascii")


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
        size = min(10000000, int(size_match.group(1)))
        writer.write(b'Z' * size)
    elif path in ["/", "/index.html"]:
        writer.write(render("It works!"))
    else:
        writer.write(render("The document could not be found."))

    writer.write_eof()


def handle_connection(connection):
    logger.info("%s Connection created" % connection_id(connection))


def handle_stream(reader, writer):
    connection = writer.get_extra_info("connection")
    stream_id = writer.get_extra_info("stream_id")
    logger.info(
        "%s Stream %d created by remote party" % (connection_id(connection), stream_id)
    )

    # we serve HTTP/0.9 on Client-Initiated Bidirectional streams
    if not stream_id % 4:
        asyncio.ensure_future(serve_http_request(reader, writer))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC server")
    parser.add_argument("--certificate", type=str, required=True)
    parser.add_argument("--host", type=str, default="::")
    parser.add_argument("--port", type=int, default=4433)
    parser.add_argument("--private-key", type=str, required=True)
    parser.add_argument("--secrets-log-file", type=str)
    parser.add_argument("--stateless-retry", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
    )

    with open(args.certificate, "rb") as fp:
        certificate = x509.load_pem_x509_certificate(
            fp.read(), backend=default_backend()
        )
    with open(args.private_key, "rb") as fp:
        private_key = serialization.load_pem_private_key(
            fp.read(), password=None, backend=default_backend()
        )

    if args.secrets_log_file:
        secrets_log_file = open(args.secrets_log_file, "a")
    else:
        secrets_log_file = None

    loop = asyncio.get_event_loop()
    protocol = loop.run_until_complete(
        aioquic.serve(
            host=args.host,
            port=args.port,
            certificate=certificate,
            private_key=private_key,
            connection_handler=handle_connection,
            stream_handler=handle_stream,
            secrets_log_file=secrets_log_file,
            stateless_retry=args.stateless_retry,
        )
    )
    loop.run_forever()
