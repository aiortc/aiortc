from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RTCIceServer:
    """
    The :class:`RTCIceServer` dictionary defines how to connect to a single
    STUN or TURN server. It includes both the URL and the necessary credentials,
    if any, to connect to the server.
    """

    urls: List[str]
    """
    This required property specifies a list of URLs which can be used to
    connect to the server.
    """
    username: Optional[str] = None
    "The username to use during authentication (for TURN only)."
    credential: Optional[str] = None
    "The credential to use during authentication (for TURN only)."
    credentialType: str = "password"

    def __post_init__(self):
        # For backwards compatibility we accept a string, but convert it to
        # a list with a single item.
        if isinstance(self.urls, str):
            self.urls = [self.urls]


@dataclass
class RTCConfiguration:
    """
    The :class:`RTCConfiguration` dictionary is used to provide configuration
    options for an :class:`RTCPeerConnection`.
    """

    iceServers: Optional[List[RTCIceServer]] = None
    "A list of :class:`RTCIceServer` objects to configure STUN / TURN servers."
