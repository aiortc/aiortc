import asyncio
import datetime

import aioice
from pyee import EventEmitter

from . import dtls
from .exceptions import InvalidAccessError, InvalidStateError
from .rtcrtpsender import RTCRtpSender
from .rtcsessiondescription import RTCSessionDescription


def get_ntp_seconds():
    return int((
        datetime.datetime.utcnow() - datetime.datetime(1900, 1, 1, 0, 0, 0)
    ).total_seconds())


class RTCPeerConnection(EventEmitter):
    def __init__(self, loop=None):
        super().__init__(loop=loop)
        self.__dtlsContext = dtls.DtlsSrtpContext()
        self.__iceConnection = None
        self.__senders = []
        self.__receivers = []

        self.__iceConnectionState = 'new'
        self.__iceGatheringState = 'new'
        self.__isClosed = False

        self.__currentLocalDescription = None
        self.__currentRemoteDescription = None

    @property
    def iceConnectionState(self):
        return self.__iceConnectionState

    @property
    def iceGatheringState(self):
        return self.__iceGatheringState

    @property
    def localDescription(self):
        return self.__currentLocalDescription

    @property
    def remoteDescription(self):
        return self.__currentRemoteDescription

    def addTrack(self, track):
        """
        Add a new media track to the set of media tracks while will be
        transmitted to the other peer.
        """
        if self.__isClosed:
            raise InvalidStateError('RTCPeerConnection is closed')

        # don't add track twice
        for sender in self.__senders:
            if sender.track == track:
                raise InvalidAccessError('Track already has a sender')

        # we only support a single track for now
        if track.kind != 'audio' or len(self.__senders):
            raise ValueError('Only a single audio track is supported for now')

        sender = RTCRtpSender(track)
        self.__senders.append(sender)
        return sender

    async def close(self):
        """
        Terminate the ICE agent, ending ICE processing and streams.
        """
        if self.__iceConnection is not None:
            await self.__iceConnection.close()
            self.__setIceConnectionState('closed')
        self.__isClosed = True

    async def createAnswer(self):
        """
        Create an SDP answer to an offer received from a remote peer during
        the offer/answer negotiation of a WebRTC connection.
        """
        return RTCSessionDescription(
            sdp=self.__createSdp(),
            type='answer')

    async def createOffer(self):
        """
        Create an SDP offer for the purpose of starting a new WebRTC
        connection to a remote peer.
        """
        self.__iceConnection = aioice.Connection(ice_controlling=True)
        self.__dtlsSession = dtls.DtlsSrtpSession(self.__dtlsContext,
                                                  is_server=True,
                                                  transport=self.__iceConnection)
        await self.__gather()

        return RTCSessionDescription(
            sdp=self.__createSdp(),
            type='offer')

    def getReceivers(self):
        return self.__receivers[:]

    def getSenders(self):
        return self.__senders[:]

    async def setLocalDescription(self, sessionDescription):
        self.__currentLocalDescription = sessionDescription

    async def setRemoteDescription(self, sessionDescription):
        if self.__iceConnection is None:
            self.__iceConnection = aioice.Connection(ice_controlling=False)
            self.__dtlsSession = dtls.DtlsSrtpSession(self.__dtlsContext,
                                                      is_server=False,
                                                      transport=self.__iceConnection)
            await self.__gather()

        for line in sessionDescription.sdp.splitlines():
            if line.startswith('a=') and ':' in line:
                attr, value = line[2:].split(':', 1)
                if attr == 'candidate':
                    self.__iceConnection.remote_candidates.append(aioice.Candidate.from_sdp(value))
                elif attr == 'fingerprint':
                    algo, fingerprint = value.split()
                    assert algo == 'sha-256'
                    self.__dtlsSession.remote_fingerprint = fingerprint
                elif attr == 'ice-ufrag':
                    self.__iceConnection.remote_username = value
                elif attr == 'ice-pwd':
                    self.__iceConnection.remote_password = value

        if self.__iceConnection.remote_candidates and self.iceConnectionState == 'new':
            asyncio.ensure_future(self.__connect())

        self.__currentRemoteDescription = sessionDescription

    async def __connect(self):
        self.__setIceConnectionState('checking')
        await self.__iceConnection.connect()
        await self.__dtlsSession.connect()
        self.__setIceConnectionState('completed')

    async def __gather(self):
        self.__setIceGatheringState('gathering')
        await self.__iceConnection.gather_candidates()
        self.__setIceGatheringState('complete')

    def __createSdp(self):
        ntp_seconds = get_ntp_seconds()
        sdp = [
            'v=0',
            'o=- %d %d IN IP4 0.0.0.0' % (ntp_seconds, ntp_seconds),
            's=-',
            't=0 0',
        ]

        default_candidate = self.__iceConnection.get_default_candidate(1)
        sdp += [
            # FIXME: negotiate codec
            'm=audio %d UDP/TLS/RTP/SAVPF 0' % default_candidate.port,
            'c=IN IP4 %s' % default_candidate.host,
            'a=rtcp:9 IN IP4 0.0.0.0',
        ]

        for candidate in self.__iceConnection.local_candidates:
            sdp += ['a=candidate:%s' % candidate.to_sdp()]
        sdp += [
            'a=ice-pwd:%s' % self.__iceConnection.local_password,
            'a=ice-ufrag:%s' % self.__iceConnection.local_username,
            'a=fingerprint:sha-256 %s' % self.__dtlsSession.local_fingerprint,
        ]
        if self.__iceConnection.ice_controlling:
            sdp += ['a=setup:actpass']
        else:
            sdp += ['a=setup:active']
        sdp += ['a=sendrecv']
        sdp += ['a=rtcp-mux']

        # FIXME: negotiate codec
        sdp += ['a=rtpmap:0 PCMU/8000']

        return '\r\n'.join(sdp) + '\r\n'

    def __setIceConnectionState(self, state):
        self.__iceConnectionState = state
        self.emit('iceconnectionstatechange')

    def __setIceGatheringState(self, state):
        self.__iceGatheringState = state
        self.emit('icegatheringstatechange')
