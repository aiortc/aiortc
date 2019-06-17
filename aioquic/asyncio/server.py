import asyncio
import ipaddress
import os
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Text, TextIO, Union, cast

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..buffer import Buffer
from ..configuration import QuicConfiguration
from ..connection import NetworkAddress, QuicConnection
from ..packet import (
    PACKET_TYPE_INITIAL,
    encode_quic_retry,
    encode_quic_version_negotiation,
    pull_quic_header,
)
from ..tls import SessionTicketFetcher, SessionTicketHandler
from .protocol import QuicConnectionProtocol, QuicStreamHandler

__all__ = ["serve"]

QuicConnectionHandler = Callable[[QuicConnectionProtocol], None]


def encode_address(addr: NetworkAddress) -> bytes:
    return ipaddress.ip_address(addr[0]).packed + bytes([addr[1] >> 8, addr[1] & 0xFF])


class QuicServer(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        configuration: QuicConfiguration,
        connection_handler: Optional[QuicConnectionHandler] = None,
        session_ticket_fetcher: Optional[SessionTicketFetcher] = None,
        session_ticket_handler: Optional[SessionTicketHandler] = None,
        stateless_retry: bool = False,
        stream_handler: Optional[QuicStreamHandler] = None,
    ) -> None:
        self._configuration = configuration
        self._protocols: Dict[bytes, QuicConnectionProtocol] = {}
        self._loop = asyncio.get_event_loop()
        self._session_ticket_fetcher = session_ticket_fetcher
        self._session_ticket_handler = session_ticket_handler
        self._transport: Optional[asyncio.DatagramTransport] = None

        if connection_handler is not None:
            self._connection_handler = connection_handler
        else:
            self._connection_handler = lambda c: None

        self._stream_handler = stream_handler

        if stateless_retry:
            self._retry_key = rsa.generate_private_key(
                public_exponent=65537, key_size=1024, backend=default_backend()
            )
        else:
            self._retry_key = None

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
            if self._retry_key is not None:
                if not header.token:
                    retry_message = encode_address(addr) + b"|" + header.destination_cid
                    retry_token = self._retry_key.public_key().encrypt(
                        retry_message,
                        padding.OAEP(
                            mgf=padding.MGF1(hashes.SHA256()),
                            algorithm=hashes.SHA256(),
                            label=None,
                        ),
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
                        retry_message = self._retry_key.decrypt(
                            header.token,
                            padding.OAEP(
                                mgf=padding.MGF1(hashes.SHA256()),
                                algorithm=hashes.SHA256(),
                                label=None,
                            ),
                        )
                        encoded_addr, original_connection_id = retry_message.split(
                            b"|", maxsplit=1
                        )
                        if encoded_addr != encode_address(addr):
                            return
                    except ValueError:
                        return

            # create new connection
            connection = QuicConnection(
                configuration=self._configuration,
                original_connection_id=original_connection_id,
                session_ticket_fetcher=self._session_ticket_fetcher,
                session_ticket_handler=self._session_ticket_handler,
            )
            protocol = QuicConnectionProtocol(
                connection, stream_handler=self._stream_handler
            )
            protocol._connection_id_issued_handler = partial(
                self._connection_id_issued, protocol=protocol
            )
            protocol._connection_id_retired_handler = partial(
                self._connection_id_retired, protocol=protocol
            )

            self._protocols[header.destination_cid] = protocol
            protocol.connection_made(self._transport)

            self._protocols[connection.host_cid] = protocol
            self._connection_handler(protocol)

        if protocol is not None:
            protocol.datagram_received(data, addr)

    def _connection_id_issued(self, cid: bytes, protocol: QuicConnectionProtocol):
        self._protocols[cid] = protocol

    def _connection_id_retired(
        self, cid: bytes, protocol: QuicConnectionProtocol
    ) -> None:
        assert self._protocols[cid] == protocol
        del self._protocols[cid]


async def serve(
    host: str,
    port: int,
    *,
    certificate: Any,
    private_key: Any,
    alpn_protocols: Optional[List[str]] = None,
    connection_handler: QuicConnectionHandler = None,
    stream_handler: QuicStreamHandler = None,
    secrets_log_file: Optional[TextIO] = None,
    session_ticket_fetcher: Optional[SessionTicketFetcher] = None,
    session_ticket_handler: Optional[SessionTicketHandler] = None,
    stateless_retry: bool = False,
) -> None:
    """
    Start a QUIC server at the given `host` and `port`.

    :func:`serve` requires a TLS certificate and private key, which can be
    specified using the following arguments:

    * ``certificate`` is the server's TLS certificate.
      See :func:`cryptography.x509.load_pem_x509_certificate`.
    * ``private_key`` is the server's private key.
      See :func:`cryptography.hazmat.primitives.serialization.load_pem_private_key`.

    :func:`serve` also accepts the following optional arguments:

    * ``connection_handler`` is a callback which is invoked whenever a
      connection is created. It must be a a function accepting a single
      argument: a :class:`~aioquic.asyncio.protocol.QuicConnectionProtocol`.
    * ``secrets_log_file`` is  a file-like object in which to log traffic
      secrets. This is useful to analyze traffic captures with Wireshark.
    * ``stateless_retry`` specifies whether a stateless retry should be
      performed prior to handling new connections.
    * ``stream_handler`` is a callback which is invoked whenever a stream is
      created. It must accept two arguments: a :class:`asyncio.StreamReader`
      and a :class:`asyncio.StreamWriter`.
    """

    loop = asyncio.get_event_loop()

    configuration = QuicConfiguration(
        alpn_protocols=alpn_protocols,
        certificate=certificate,
        is_client=False,
        private_key=private_key,
        secrets_log_file=secrets_log_file,
    )

    _, protocol = await loop.create_datagram_endpoint(
        lambda: QuicServer(
            configuration=configuration,
            connection_handler=connection_handler,
            session_ticket_fetcher=session_ticket_fetcher,
            session_ticket_handler=session_ticket_handler,
            stateless_retry=stateless_retry,
            stream_handler=stream_handler,
        ),
        local_addr=(host, port),
    )
