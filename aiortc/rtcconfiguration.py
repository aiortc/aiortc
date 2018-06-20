import attr


@attr.s
class RTCConfiguration:
    """
    The :class:`RTCConfiguration` dictionary is used to provide configuration
    options for an :class:`RTCPeerConnection`.
    """
    iceServers = attr.ib(default=None)
    "A list of :class:`RTCIceServer` objects to configure STUN / TURN servers."


@attr.s
class RTCIceServer:
    """
    The :class:`RTCIceServer` dictionary defines how to connect to a single
    STUN or TURN server. It includes both the URL and the necessary credentials,
    if any, to connect to the server.
    """
    urls = attr.ib()
    """
    This required property is either a single string or a list of strings,
    each specifying a URL which can be used to connect to the server.
    """
    username = attr.ib(default=None)
    "The username to use during authentication (for TURN only)."
    credential = attr.ib(default=None)
    "The credential to use during authentication (for TURN only)."
    credentialType = attr.ib(default='password')
