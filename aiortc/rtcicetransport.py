from aioice import Connection


class RTCIceGatherer:
    def __init__(self):
        self._connection = Connection(ice_controlling=False,
                                      stun_server=('stun.l.google.com', 19302))

    async def gather(self):
        await self._connection.gather_candidates()

    def getLocalCandidates(self):
        return self._connection.local_candidates

    def getLocalParameters(self):
        return RTCIceParameters(
            usernameFragment=self._connection.local_username,
            password=self._connection.local_password)


class RTCIceParameters:
    def __init__(self, usernameFragment=None, password=None):
        self.usernameFragment = usernameFragment
        self.password = password


class RTCIceTransport:
    def __init__(self, gatherer):
        self._iceGatherer = gatherer

    @property
    def iceGatherer(self):
        return self._iceGatherer

    @property
    def role(self):
        if self._iceGatherer._connection.ice_controlling:
            return 'controlling'
        else:
            return 'controlled'

    def getRemoteCandidates(self):
        return self._iceGatherer._connection.remote_candidates

    def setRemoteCandidates(self, remoteCandidates):
        self._iceGatherer._connection.remote_candidates = remoteCandidates
