import enum
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class RTCIceServer:
    """
    The :class:`RTCIceServer` dictionary defines how to connect to a single
    STUN or TURN server. It includes both the URL and the necessary credentials,
    if any, to connect to the server.
    """

    urls: Union[str, list[str]]
    """
    This required property is either a single string or a list of strings,
    each specifying a URL which can be used to connect to the server.
    """
    username: Optional[str] = None
    "The username to use during authentication (for TURN only)."
    credential: Optional[str] = None
    "The credential to use during authentication (for TURN only)."
    credentialType: str = "password"


@dataclass
class RTCSocks5Proxy:
    """
    The :class:`RTCSocks5Proxy` dictionary defines how to connect to a
    SOCKS5 proxy server. It includes the server address and the necessary 
    credentials, if any, to authenticate with the server.
    """

    host: str
    "The hostname or IP address of the SOCKS5 proxy server."
    port: int
    "The port of the SOCKS5 proxy server."
    username: Optional[str] = None
    "The username to use during authentication (if required)."
    password: Optional[str] = None
    "The password to use during authentication (if required)."


class RTCBundlePolicy(enum.Enum):
    """
    The :class:`RTCBundlePolicy` affects which media tracks are negotiated if
    the remote endpoint is not bundle-aware, and what ICE candidates are
    gathered.

    See https://w3c.github.io/webrtc-pc/#rtcbundlepolicy-enum
    """

    BALANCED = "balanced"
    """
    Gather ICE candidates for each media type in use (audio, video, and data).
    If the remote endpoint is not bundle-aware, negotiate only one audio and
    video track on separate transports.
    """

    MAX_COMPAT = "max-compat"
    """
    Gather ICE candidates for each track. If the remote endpoint is not
    bundle-aware, negotiate all media tracks on separate transports.
    """

    MAX_BUNDLE = "max-bundle"
    """
    Gather ICE candidates for only one track. If the remote endpoint is not
    bundle-aware, negotiate only one media track.
    """


@dataclass
class RTCConfiguration:
    """
    The :class:`RTCConfiguration` dictionary is used to provide configuration
    options for an :class:`RTCPeerConnection`.
    """

    iceServers: Optional[list[RTCIceServer]] = None
    "A list of :class:`RTCIceServer` objects to configure STUN / TURN servers."

    bundlePolicy: RTCBundlePolicy = RTCBundlePolicy.BALANCED
    "The media-bundling policy to use when gathering ICE candidates."
    
    socks5Proxy: Optional[RTCSocks5Proxy] = None
    "A :class:`RTCSocks5Proxy` object to configure a SOCKS5 proxy for all UDP traffic."
