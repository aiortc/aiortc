import attr
from aioice import Candidate, Connection
from pyee import EventEmitter

from .utils import parse_stun_turn_uri


@attr.s
class RTCIceCandidate:
    component = attr.ib()
    foundation = attr.ib()
    ip = attr.ib()
    port = attr.ib()
    priority = attr.ib()
    protocol = attr.ib()
    type = attr.ib()
    sdpMLineIndex = attr.ib(default=None)
    tcpType = attr.ib(default=None)


def candidate_from_aioice(x):
    return RTCIceCandidate(
        component=x.component,
        foundation=x.foundation,
        ip=x.host,
        port=x.port,
        priority=x.priority,
        protocol=x.transport,
        tcpType=x.tcptype,
        type=x.type)


def candidate_to_aioice(x):
    return Candidate(
        component=x.component,
        foundation=x.foundation,
        host=x.ip,
        port=x.port,
        priority=x.priority,
        transport=x.protocol,
        tcptype=x.tcpType,
        type=x.type)


class RTCIceGatherer(EventEmitter):
    """
    The :class:`RTCIceGatherer` interface gathers local host, server reflexive
    and relay candidates, as well as enabling the retrieval of local
    Interactive Connectivity Establishment (ICE) parameters which can be
    exchanged in signaling.
    """
    def __init__(self, servers=None):
        super().__init__()
        ice_kargs = {}
        for server in servers or []:
            uri = parse_stun_turn_uri(server.urls)

            if uri['scheme'] == 'stun':
                if 'stun_server' in ice_kargs:
                    # do not suport multiples stun server. ignoring
                    continue
                ice_kargs['stun_server'] = (uri['host'], uri['port'] or 3478)
            elif uri['scheme'] == 'turn':
                if uri['transport'] and uri['transport'] != 'udp':
                    # only suport udp transport. ignoring
                    continue
                if 'turn_server' in ice_kargs:
                    # do not suport multiples turn server. ignoring
                    continue
                if server.credentialType != "password":
                    # only suport credentialType password. ignoring
                    continue
                ice_kargs['turn_server'] = (uri['host'], uri['port'] or 3478)
                ice_kargs['turn_username'] = server.username
                ice_kargs['turn_password'] = server.credential

            # ignoring unsuported schema as stuns and turns
        self._connection = Connection(ice_controlling=False, **ice_kargs)
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
        return [candidate_from_aioice(x) for x in self._connection.local_candidates]

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

    :param: gatherer: An :class:`RTCIceGatherer`.
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
        The current role of the ICE transport: `'controlling'` or `'controlled'`.
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

    def addRemoteCandidate(self, candidate):
        """
        Add a remote candidate.
        """
        self._connection.remote_candidates += [candidate_to_aioice(candidate)]

    def getRemoteCandidates(self):
        """
        Retrieve the list of candidates associated with the remote
        :class:`RTCIceTransport`.
        """
        return [candidate_from_aioice(x) for x in self._connection.remote_candidates]

    def setRemoteCandidates(self, remoteCandidates):
        """
        Set the list of candidates associated with the remote
        :class:`RTCIceTransport`.
        """
        self._connection.remote_candidates = [candidate_to_aioice(x) for x in remoteCandidates]

    async def start(self, remoteParameters):
        """
        Initiate connectivity checks.

        :param: remoteParameters: The :class:`RTCIceParameters` associated with
                                  the remote :class:`RTCIceTransport`.
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
