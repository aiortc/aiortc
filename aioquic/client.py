import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Optional, TextIO, cast

from .connection import QuicConnection, QuicStreamHandler
from .tls import SessionTicket, SessionTicketHandler

__all__ = ["connect"]


@asynccontextmanager
async def connect(
    host: str,
    port: int,
    *,
    alpn_protocols: Optional[List[str]] = None,
    protocol_version: Optional[int] = None,
    secrets_log_file: Optional[TextIO] = None,
    session_ticket: Optional[SessionTicket] = None,
    session_ticket_handler: Optional[SessionTicketHandler] = None,
    stream_handler: Optional[QuicStreamHandler] = None,
) -> AsyncGenerator[QuicConnection, None]:
    """
    Connect to a QUIC server at the given `host` and `port`.

    :meth:`connect()` returns an awaitable. Awaiting it yields a
    :class:`~aioquic.QuicConnection` which can be used to create streams.

    :func:`connect` also accepts the following optional arguments:

    * ``alpn_protocols`` is a list of ALPN protocols to offer in the
      ClientHello.
    * ``secrets_log_file`` is a file-like object in which to log traffic
      secrets. This is useful to analyze traffic captures with Wireshark.
    * ``session_ticket`` is a TLS session ticket which should be used for
      resumption.
    * ``session_ticket_handler`` is a callback which is invoked by the TLS
      engine when a new session ticket is received.
    * ``stream_handler`` is a callback which is invoked whenever a stream is
      created. It must accept two arguments: a :class:`asyncio.StreamReader`
      and a :class:`asyncio.StreamWriter`.
    """
    loop = asyncio.get_event_loop()

    # if host is not an IP address, pass it to enable SNI
    try:
        ipaddress.ip_address(host)
        server_name = None
    except ValueError:
        server_name = host

    # lookup remote address
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    addr = infos[0][4]
    if len(addr) == 2:
        addr = ("::ffff:" + addr[0], addr[1], 0, 0)

    # connect
    _, protocol = await loop.create_datagram_endpoint(
        lambda: QuicConnection(
            alpn_protocols=alpn_protocols,
            is_client=True,
            secrets_log_file=secrets_log_file,
            server_name=server_name,
            session_ticket=session_ticket,
            session_ticket_handler=session_ticket_handler,
            stream_handler=stream_handler,
        ),
        local_addr=("::", 0),
    )
    protocol = cast(QuicConnection, protocol)
    protocol.connect(addr, protocol_version=protocol_version)
    await protocol.wait_connected()
    try:
        yield protocol
    finally:
        protocol.close()
