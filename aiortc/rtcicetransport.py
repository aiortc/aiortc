import attr
from aioice import Candidate as RTCIceCandidate  # noqa
from aioice import Connection
from pyee import EventEmitter


class RTCIceGatherer(EventEmitter):
    """
    The :class:`RTCIceGatherer` interface gathers local host, server reflexive
    and relay candidates, as well as enabling the retrieval of local
    Interactive Connectivity Establishment (ICE) parameters which can be
    exchanged in signaling.
    """
    def __init__(self):
        super().__init__()
        self._connection = Connection(ice_controlling=False,
                                      stun_server=('stun.l.google.com', 19302))
        self.__state = 'new'

    @property
    def state(self):
        """
        The current state of the ICE gatherer.
        """
        return self.__state

    async def gather(self):
        """
        Gather ICE candidates.
        """
        if self.__state == 'new':
            self.__setState('gathering')
            await self._connection.gather_candidates()
            self.__setState('completed')

    def getLocalCandidates(self):
        """
        Retrieve the list of valid local candidates associated with the ICE
        gatherer.
        """
        return self._connection.local_candidates

    def getLocalParameters(self):
        """
        Retrieve the ICE parameters of the ICE gatherer.

        :rtype: RTCIceParameters
        """
        return RTCIceParameters(
            usernameFragment=self._connection.local_username,
            password=self._connection.local_password)

    def __setState(self, state):
        self.__state = state
        self.emit('statechange')


@attr.s
class RTCIceParameters:
    """
    The :class:`RTCIceParameters` dictionary includes the ICE username
    fragment and password and other ICE-related parameters.
    """
    usernameFragment = attr.ib(default=None)
    "ICE username fragment."

    password = attr.ib(default=None)
    "ICE password."


class RTCIceTransport(EventEmitter):
    """
    The :class:`RTCIceTransport` interface allows an application access to
    information about the Interactive Connectivity Establishment (ICE)
    transport over which packets are sent and received.
    """
    def __init__(self, gatherer):
        super().__init__()
        self.__iceGatherer = gatherer
        self.__state = 'new'

    @property
    def iceGatherer(self):
        """
        The ICE gatherer passed in the constructor.
        """
        return self.__iceGatherer

    @property
    def role(self):
        """
        The current role of the ICE transport: `"controlling"` or `"controlled"`.
        """
        if self._connection.ice_controlling:
            return 'controlling'
        else:
            return 'controlled'

    @property
    def state(self):
        """
        The current state of the ICE transport.
        """
        return self.__state

    def getRemoteCandidates(self):
        """
        Retrieve the list of candidates associated with the remote
        :class:`RTCIceTransport`.
        """
        return self._connection.remote_candidates

    def setRemoteCandidates(self, remoteCandidates):
        """
        Set the list of candidates associated with the remote
        :class:`RTCIceTransport`.
        """
        self._connection.remote_candidates = remoteCandidates

    async def start(self, remoteParameters):
        """
        Initiate connectivity checks.
        """
        self.__setState('checking')
        self._connection.remote_username = remoteParameters.usernameFragment
        self._connection.remote_password = remoteParameters.password
        await self._connection.connect()
        self.__setState('completed')

    async def stop(self):
        """
        Irreversibly stop the :class:`RTCIceTransport`.
        """
        if self.state != 'closed':
            self.__setState('closed')
            await self._connection.close()

    @property
    def _connection(self):
        return self.iceGatherer._connection

    def __setState(self, state):
        self.__state = state
        self.emit('statechange')
