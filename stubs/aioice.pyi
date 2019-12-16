from typing import List


class Candidate:
    component: int
    foundation: str
    host: str
    port: int
    priority: int
    transport: str
    related_address: str
    related_port: int
    tcptype: int
    type: int
    generation: str

    def __init__(
            self,
            foundation: str,
            component: int,
            transport: str,
            priority: int,
            host: str,
            port: int,
            type: str,
            related_address: str = ...,
            related_port: int = ...,
            tcptype: int = ...,
            generation: str = ...,
    ): ...


class Connection:
    ice_controlling: bool
    remote_username: str
    remote_password: str
    _remote_candidates_end: bool

    def __init__(
            self,
            ice_controlling: bool,
            components: int = ...,
            stun_server: str = ...,
            turn_server: str = ...,
            turn_username: str = ...,
            turn_password: str = ...,
            turn_ssl: bool = ...,
            turn_transport: str = ...,
            use_ipv4: bool = ...,
            use_ipv6: bool = ...,
    ): ...
    @property
    def local_candidates(self) -> List[Candidate]: ...
    @property
    def remote_candidates(self) -> List[Candidate]: ...
    @property
    def local_username(self) -> str: ...
    @property
    def local_password(self) -> str: ...
    def add_remote_candidate(self, remote_candidate: Candidate) -> None: ...
    async def gather_candidates(self) -> None: ...
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def recv(self) -> bytes: ...
    async def send(self, date: bytes) -> None: ...
