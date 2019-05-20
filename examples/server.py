import argparse
import asyncio
import binascii
import logging
import os

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


async def serve_http_request(reader, writer):
    """
    Serve an HTTP/0.9 request.
    """
    request = await reader.read()

    if request == b"GET /\r\n" or request == "GET /index.html\r\n":
        writer.write(TEMPLATE.format(content="It works!").encode("utf8"))
    elif request == b"GET /5000000\r\n":
        writer.write(os.urandom(5000000))
    elif request == b"GET /10000000\r\n":
        writer.write(os.urandom(10000000))
    else:
        writer.write(
            TEMPLATE.format(content="The document could not be found.").encode("utf8")
        )

    writer.write_eof()


async def handle_connection(connection):
    def stream_created(reader, writer):
        connection = writer.get_extra_info("connection")
        stream_id = writer.get_extra_info("stream_id")
        logger.info(
            "%s Stream %d created by remote party"
            % (connection_id(connection), stream_id)
        )

        # we serve HTTP/0.9 on Client-Initiated Bidirectional streams
        if not stream_id % 4:
            asyncio.ensure_future(serve_http_request(reader, writer))

    connection.stream_created_cb = stream_created


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
            handle_connection,
            host=args.host,
            port=args.port,
            certificate=certificate,
            private_key=private_key,
            secrets_log_file=secrets_log_file,
            stateless_retry=args.stateless_retry,
        )
    )
    loop.run_forever()
