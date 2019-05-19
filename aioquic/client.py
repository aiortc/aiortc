import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Optional, TextIO, cast

from .connection import QuicConnection

__all__ = ["connect"]


@asynccontextmanager
async def connect(
    host: str,
    port: int,
    *,
    alpn_protocols: Optional[List[str]] = None,
    secrets_log_file: TextIO = None,
) -> AsyncGenerator[QuicConnection, None]:
    """
    Connect to a QUIC server at the given `host` and `port`.

    :meth:`connect()` returns an awaitable. Awaiting it yields a
    :class:`~aioquic.QuicConnection` which can be used to create streams.

    :param: alpn_protocols: a list of ALPN protocols to offer in the ClientHello.
    :param: secrets_log_file: a file-like object in which to log traffic secrets. This is useful
                              to analyze traffic captures with Wireshark.
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
            alpn_protocols=alpn_protocols, is_client=True, server_name=server_name
        ),
        local_addr=("::", 0),
    )
    protocol = cast(QuicConnection, protocol)
    await protocol.connect(addr)
    try:
        yield protocol
    finally:
        protocol.close()
