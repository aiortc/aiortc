import asyncio
import os
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Text, TextIO, Union, cast

from ..buffer import Buffer
from ..quic.configuration import QuicConfiguration
from ..quic.connection import NetworkAddress, QuicConnection
from ..quic.logger import QuicLogger
from ..quic.packet import (
    PACKET_TYPE_INITIAL,
    encode_quic_retry,
    encode_quic_version_negotiation,
    pull_quic_header,
)
from ..quic.retry import QuicRetryTokenHandler
from ..tls import SessionTicketFetcher, SessionTicketHandler
from .protocol import QuicConnectionProtocol, QuicStreamHandler

__all__ = ["serve"]

QuicConnectionHandler = Callable[[QuicConnectionProtocol], None]


class QuicServer(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        configuration: QuicConfiguration,
        session_ticket_fetcher: Optional[SessionTicketFetcher] = None,
        session_ticket_handler: Optional[SessionTicketHandler] = None,
        stateless_retry: bool = False,
        stream_handler: Optional[QuicStreamHandler] = None,
    ) -> None:
        self._configuration = configuration
        self._loop = asyncio.get_event_loop()
        self._protocols: Dict[bytes, QuicConnectionProtocol] = {}
        self._session_ticket_fetcher = session_ticket_fetcher
        self._session_ticket_handler = session_ticket_handler
        self._transport: Optional[asyncio.DatagramTransport] = None

        self._stream_handler = stream_handler

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
            protocol = QuicConnectionProtocol(
                connection, stream_handler=self._stream_handler
            )
            protocol.connection_made(self._transport)

            protocol._connection_id_issued_handler = partial(
                self._connection_id_issued, protocol=protocol
            )
            protocol._connection_id_retired_handler = partial(
                self._connection_id_retired, protocol=protocol
            )

            self._protocols[header.destination_cid] = protocol
            self._protocols[connection.host_cid] = protocol

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
    idle_timeout: Optional[float] = None,
    quic_logger: Optional[QuicLogger] = None,
    stream_handler: QuicStreamHandler = None,
    secrets_log_file: Optional[TextIO] = None,
    session_ticket_fetcher: Optional[SessionTicketFetcher] = None,
    session_ticket_handler: Optional[SessionTicketHandler] = None,
    stateless_retry: bool = False,
) -> QuicServer:
    """
    Start a QUIC server at the given `host` and `port`.

    :func:`serve` requires a TLS certificate and private key, which can be
    specified using the following arguments:

    * ``certificate`` is the server's TLS certificate.
      See :func:`cryptography.x509.load_pem_x509_certificate`.
    * ``private_key`` is the server's private key.
      See :func:`cryptography.hazmat.primitives.serialization.load_pem_private_key`.

    :func:`serve` also accepts the following optional arguments:

    * ``secrets_log_file`` is  a file-like object in which to log traffic
      secrets. This is useful to analyze traffic captures with Wireshark.
    * ``session_ticket_fetcher`` is a callback which is invoked by the TLS
      engine when a session ticket is presented by the peer. It should return
      the session ticket with the specified ID or `None` if it is not found.
    * ``session_ticket_handler`` is a callback which is invoked by the TLS
      engine when a new session ticket is issued. It should store the session
      ticket for future lookup.
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
        quic_logger=quic_logger,
        secrets_log_file=secrets_log_file,
    )
    if idle_timeout is not None:
        configuration.idle_timeout = idle_timeout

    _, protocol = await loop.create_datagram_endpoint(
        lambda: QuicServer(
            configuration=configuration,
            session_ticket_fetcher=session_ticket_fetcher,
            session_ticket_handler=session_ticket_handler,
            stateless_retry=stateless_retry,
            stream_handler=stream_handler,
        ),
        local_addr=(host, port),
    )
    return cast(QuicServer, protocol)
