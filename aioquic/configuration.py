from dataclasses import dataclass, field
from typing import Any, List, Optional, TextIO

from . import tls
from .packet import QuicProtocolVersion


@dataclass
class QuicConfiguration:
    alpn_protocols: Optional[List[str]] = None
    certificate: Any = None
    idle_timeout: float = 60.0
    is_client: bool = True
    private_key: Any = None
    secrets_log_file: TextIO = None
    server_name: Optional[str] = None
    session_ticket: Optional[tls.SessionTicket] = None
    supported_versions: List[QuicProtocolVersion] = field(
        default_factory=lambda: [
            QuicProtocolVersion.DRAFT_19,
            QuicProtocolVersion.DRAFT_20,
        ]
    )
