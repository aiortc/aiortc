import attr


@attr.s
class RTCConfiguration:
    bundlePolicy = attr.ib(default='max-compat')
    iceServers = attr.ib(default=None)


@attr.s
class RTCIceServer:
    urls = attr.ib()
    username = attr.ib(default=None)
    credential = attr.ib(default=None)
    credentialType = attr.ib(default='password')
