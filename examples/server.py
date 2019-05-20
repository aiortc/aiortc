import argparse
import asyncio
import binascii
import ipaddress
import logging
import os

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from aioquic.connection import QuicConnection
from aioquic.packet import (
    PACKET_TYPE_INITIAL,
    encode_quic_retry,
    encode_quic_version_negotiation,
    pull_quic_header,
)
from aioquic.tls import Buffer

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


class QuicServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, retry=False, **kwargs):
        self._connections = {}
        self._kwargs = kwargs
        self._retry_key = None
        self._transport = None

        if retry:
            self._retry_key = rsa.generate_private_key(
                public_exponent=65537, key_size=512, backend=default_backend()
            )

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, datagram, addr):
        buf = Buffer(data=datagram)
        header = pull_quic_header(buf, host_cid_length=8)

        # version negotiation
        if (
            header.version is not None
            and header.version not in QuicConnection.supported_versions
        ):
            self._transport.sendto(
                encode_quic_version_negotiation(
                    source_cid=header.destination_cid,
                    destination_cid=header.source_cid,
                    supported_versions=QuicConnection.supported_versions,
                ),
                addr,
            )
            return

        connection = self._connections.get(header.destination_cid, None)
        if connection is None and header.packet_type == PACKET_TYPE_INITIAL:
            # stateless retry
            if self._retry_key is not None:
                retry_message = str(addr).encode("ascii")
                if not header.token:
                    logger.info("Sending retry to %s" % (addr,))
                    retry_token = self._retry_key.sign(
                        retry_message,
                        padding.PSS(
                            mgf=padding.MGF1(hashes.SHA256()),
                            salt_length=padding.PSS.MAX_LENGTH,
                        ),
                        hashes.SHA256(),
                    )
                    self._transport.sendto(
                        encode_quic_retry(
                            version=header.version,
                            source_cid=os.urandom(8),
                            destination_cid=header.source_cid,
                            original_destination_cid=header.destination_cid,
                            retry_token=retry_token,
                        ),
                        addr,
                    )
                    return
                else:
                    try:
                        self._retry_key.public_key().verify(
                            header.token,
                            retry_message,
                            padding.PSS(
                                mgf=padding.MGF1(hashes.SHA256()),
                                salt_length=padding.PSS.MAX_LENGTH,
                            ),
                            hashes.SHA256(),
                        )
                    except InvalidSignature:
                        return

            # create new connection
            connection = QuicConnection(is_client=False, **self._kwargs)
            connection.connection_made(self._transport)
            connection.stream_created_cb = self.stream_created
            self._connections[connection.host_cid] = connection
            logger.info("%s New connection from %s" % (connection_id(connection), addr))

        if connection is not None:
            connection.datagram_received(datagram, addr)

    def stream_created(self, reader, writer):
        connection = writer.get_extra_info("connection")
        stream_id = writer.get_extra_info("stream_id")
        logger.info(
            "%s Stream %d created by remote party"
            % (connection_id(connection), stream_id)
        )

        # we serve HTTP/0.9 on Client-Initiated Bidirectional streams
        if not stream_id % 4:
            asyncio.ensure_future(serve_http_request(reader, writer))


async def run(host, port, **kwargs):
    # if host is not an IP address, pass it to enable SNI
    try:
        ipaddress.ip_address(host)
    except ValueError:
        kwargs["server_name"] = host

    _, protocol = await loop.create_datagram_endpoint(
        lambda: QuicServerProtocol(**kwargs), local_addr=(host, port)
    )
    logger.info("Listening on %s port %s" % (host, port))
    return protocol


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC server")
    parser.add_argument("--certificate", type=str, required=True)
    parser.add_argument("--host", type=str, default="::")
    parser.add_argument("--port", type=int, default=4433)
    parser.add_argument("--private-key", type=str, required=True)
    parser.add_argument("--secrets-log-file", type=str)
    parser.add_argument("--retry", action="store_true", help="Use stateless retry.")
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
        run(
            host=args.host,
            port=args.port,
            alpn_protocols=["http/0.9"],
            certificate=certificate,
            private_key=private_key,
            secrets_log_file=secrets_log_file,
            retry=args.retry,
        )
    )
    loop.run_forever()
