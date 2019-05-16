import argparse
import asyncio
import binascii
import ipaddress
import logging

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from aioquic.connection import QuicConnection
from aioquic.packet import encode_quic_version_negotiation, pull_quic_header
from aioquic.tls import Buffer

logger = logging.getLogger("server")


def connection_id(connection):
    return "Connection %s" % binascii.hexlify(connection.host_cid).decode("ascii")


async def serve_http_request(reader, writer):
    """
    Serve an HTTP/0.9 request.
    """
    request = await reader.read()

    if request == b"GET /\r\n":
        writer.write(b"It works!\r\n")
    else:
        writer.write(b"Not found\r\n")

    writer.write_eof()


class QuicConnectionTransport:
    def __init__(self, protocol, addr):
        self.__addr = addr
        self.__protocol = protocol

    def sendto(self, datagram):
        self.__protocol._transport.sendto(datagram, self.__addr)


class QuicServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, **kwargs):
        self._connections = {}
        self._kwargs = kwargs
        self._transport = None

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
        if connection is None and header.is_long_header:
            # create new connection
            connection = QuicConnection(is_client=False, **self._kwargs)
            connection.connection_made(QuicConnectionTransport(self, addr))
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
        if stream_id == 0:
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
        )
    )
    loop.run_forever()
