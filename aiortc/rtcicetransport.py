from aioice import Candidate as RTCIceCandidate  # noqa
from aioice import Connection
from pyee import EventEmitter


class RTCIceGatherer(EventEmitter):
    def __init__(self):
        super().__init__()
        self._connection = Connection(ice_controlling=False,
                                      stun_server=('stun.l.google.com', 19302))
        self.__state = 'new'

    @property
    def state(self):
        return self.__state

    async def gather(self):
        self.__setState('gathering')
        await self._connection.gather_candidates()
        self.__setState('complete')

    def getLocalCandidates(self):
        return self._connection.local_candidates

    def getLocalParameters(self):
        return RTCIceParameters(
            usernameFragment=self._connection.local_username,
            password=self._connection.local_password)

    def __setState(self, state):
        self.__state = state
        self.emit('statechange')


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
        if self._connection.ice_controlling:
            return 'controlling'
        else:
            return 'controlled'

    @property
    def _connection(self):
        return self._iceGatherer._connection

    def getRemoteCandidates(self):
        return self._connection.remote_candidates

    def setRemoteCandidates(self, remoteCandidates):
        self._connection.remote_candidates = remoteCandidates

    async def start(self, remoteParameters):
        self._connection.remote_username = remoteParameters.usernameFragment
        self._connection.remote_password = remoteParameters.password
        await self._connection.connect()

    async def stop(self):
        await self._connection.close()
