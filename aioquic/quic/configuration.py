from dataclasses import dataclass, field
from typing import Any, List, Optional, TextIO

from ..tls import SessionTicket
from .logger import QuicLogger
from .packet import QuicProtocolVersion


@dataclass
class QuicConfiguration:
    """
    A QUIC configuration.
    """

    alpn_protocols: Optional[List[str]] = None
    """
    A list of supported ALPN protocols.
    """

    certificate: Any = None
    """
    The server's TLS certificate.

    See :func:`cryptography.x509.load_pem_x509_certificate`.

    .. note:: This is only used by servers.
    """

    idle_timeout: float = 60.0
    """
    The idle timeout in seconds.

    The connection is terminated if nothing is received for the given duration.
    """

    is_client: bool = True
    """
    Whether this is the client side of the QUIC connection.
    """

    private_key: Any = None
    """
    The server's TLS private key.

    See :func:`cryptography.hazmat.primitives.serialization.load_pem_private_key`.

    .. note:: This is only used by servers.
    """

    quic_logger: Optional[QuicLogger] = None
    """
    The :class:`~aioquic.quic.logger.QuicLogger` instance to log events to.
    """

    secrets_log_file: TextIO = None
    """
    A file-like object in which to log traffic secrets.

    This is useful to analyze traffic captures with Wireshark.
    """

    server_name: Optional[str] = None
    """
    The server name to send during the TLS handshake the Server Name Indication.

    .. note:: This is only used by clients.
    """

    session_ticket: Optional[SessionTicket] = None
    """
    The TLS session ticket which should be used for session resumption.
    """

    supported_versions: List[int] = field(
        default_factory=lambda: [QuicProtocolVersion.DRAFT_22]
    )
