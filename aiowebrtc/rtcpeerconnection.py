import asyncio
import datetime

import aioice
from pyee import EventEmitter

from . import dtls, sdp
from .exceptions import InvalidAccessError, InvalidStateError
from .rtcrtptransceiver import RTCRtpReceiver, RTCRtpSender, RTCRtpTransceiver
from .rtcsessiondescription import RTCSessionDescription


DUMMY_CANDIDATE = aioice.Candidate(
    foundation='',
    component=1,
    transport='udp',
    priority=1,
    host='0.0.0.0',
    port=0,
    type='host')
MEDIA_KINDS = ['audio', 'video']


def get_ntp_seconds():
    return int((
        datetime.datetime.utcnow() - datetime.datetime(1900, 1, 1, 0, 0, 0)
    ).total_seconds())


class RTCPeerConnection(EventEmitter):
    def __init__(self, loop=None):
        super().__init__(loop=loop)
        self.__dtlsContext = dtls.DtlsSrtpContext()
        self.__transceivers = []

        self.__iceConnectionState = 'new'
        self.__iceGatheringState = 'new'
        self.__isClosed = False
        self.__signalingState = 'stable'

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

    @property
    def signalingState(self):
        return self.__signalingState

    def addTrack(self, track):
        """
        Add a new media track to the set of media tracks while will be
        transmitted to the other peer.
        """
        # check state is valid
        self.__assertNotClosed()
        if track.kind not in ['audio', 'video']:
            raise ValueError('Invalid track kind "%s"' % track.kind)

        # don't add track twice
        for sender in self.getSenders():
            if sender.track == track:
                raise InvalidAccessError('Track already has a sender')

        for transceiver in self.__transceivers:
            if transceiver._kind == track.kind and transceiver.sender.track is None:
                transceiver.sender._track = track
                return transceiver.sender

        # we only support a single media track for now
        if len(self.__transceivers):
            raise ValueError('Only a single media track is supported for now')

        transceiver = RTCRtpTransceiver(
            receiver=RTCRtpReceiver(),
            sender=RTCRtpSender(track))
        transceiver._kind = track.kind
        transceiver._iceConnection = aioice.Connection(ice_controlling=True)
        transceiver._dtlsSession = dtls.DtlsSrtpSession(self.__dtlsContext,
                                                        is_server=True,
                                                        transport=transceiver._iceConnection)
        self.__transceivers.append(transceiver)
        return transceiver.sender

    async def close(self):
        """
        Terminate the ICE agent, ending ICE processing and streams.
        """
        if self.__isClosed:
            return
        self.__isClosed = True
        self.__setSignalingState('closed')
        for transceiver in self.__transceivers:
            await transceiver._iceConnection.close()
        self.__setIceConnectionState('closed')

    async def createAnswer(self):
        """
        Create an SDP answer to an offer received from a remote peer during
        the offer/answer negotiation of a WebRTC connection.
        """
        # check state is valid
        self.__assertNotClosed()
        if self.signalingState not in ['have-remote-offer', 'have-local-pranswer']:
            raise InvalidStateError('Cannot create answer in signaling state "%s"' %
                                    self.signalingState)

        return RTCSessionDescription(
            sdp=self.__createSdp(),
            type='answer')

    async def createOffer(self):
        """
        Create an SDP offer for the purpose of starting a new WebRTC
        connection to a remote peer.
        """
        # check state is valid
        self.__assertNotClosed()

        return RTCSessionDescription(
            sdp=self.__createSdp(),
            type='offer')

    def getReceivers(self):
        return list(map(lambda x: x.receiver, self.__transceivers))

    def getSenders(self):
        return list(map(lambda x: x.sender, self.__transceivers))

    async def setLocalDescription(self, sessionDescription):
        if sessionDescription.type == 'offer':
            self.__setSignalingState('have-local-offer')
        elif sessionDescription.type == 'answer':
            self.__setSignalingState('stable')

        # gather
        await self.__gather()

        # connect
        asyncio.ensure_future(self.__connect())

        self.__currentLocalDescription = RTCSessionDescription(
            sdp=self.__createSdp(),
            type=sessionDescription.type)

    async def setRemoteDescription(self, sessionDescription):
        # check description is compatible with signaling state
        if sessionDescription.type == 'offer':
            if self.signalingState not in ['stable', 'have-remote-offer']:
                raise InvalidStateError('Cannot handle offer in signaling state "%s"' %
                                        self.signalingState)
        elif sessionDescription.type == 'answer':
            if self.signalingState not in ['have-local-offer', 'have-remote-pranswer']:
                raise InvalidStateError('Cannot handle answer in signaling state "%s"' %
                                        self.signalingState)

        # parse description
        parsedRemoteDescription = sdp.SessionDescription.parse(sessionDescription.sdp)

        # apply description
        for media in parsedRemoteDescription.media:
            if media.kind not in ['audio', 'video']:
                continue

            # find transceiver
            transceiver = None
            for t in self.__transceivers:
                if t._kind == media.kind:
                    transceiver = t
            if transceiver is None:
                transceiver = RTCRtpTransceiver(
                    sender=RTCRtpSender(),
                    receiver=RTCRtpReceiver())
                transceiver._iceConnection = aioice.Connection(ice_controlling=False)
                transceiver._dtlsSession = dtls.DtlsSrtpSession(
                    self.__dtlsContext,
                    is_server=False,
                    transport=transceiver._iceConnection)
                transceiver._kind = media.kind
                self.__transceivers.append(transceiver)

            # configure transport
            transceiver._iceConnection.remote_candidates = media.ice_candidates
            transceiver._iceConnection.remote_username = media.ice_ufrag
            transceiver._iceConnection.remote_password = media.ice_pwd
            transceiver._dtlsSession.remote_fingerprint = media.dtls_fingerprint

        # connect
        asyncio.ensure_future(self.__connect())

        # update signaling state
        if sessionDescription.type == 'offer':
            self.__setSignalingState('have-remote-offer')
        elif sessionDescription.type == 'answer':
            self.__setSignalingState('stable')

        self.__currentRemoteDescription = sessionDescription

    async def __connect(self):
        for transceiver in self.__transceivers:
            if (not transceiver._iceConnection.local_candidates or
               not transceiver._iceConnection.remote_candidates):
                return

        if self.iceConnectionState == 'new':
            self.__setIceConnectionState('checking')
            for transceiver in self.__transceivers:
                await transceiver._iceConnection.connect()
                await transceiver._dtlsSession.connect()
            self.__setIceConnectionState('completed')

    async def __gather(self):
        if self.__iceGatheringState == 'new':
            self.__setIceGatheringState('gathering')
            for transceiver in self.__transceivers:
                await transceiver._iceConnection.gather_candidates()
            self.__setIceGatheringState('complete')

    def __assertNotClosed(self):
        if self.__isClosed:
            raise InvalidStateError('RTCPeerConnection is closed')

    def __createSdp(self):
        ntp_seconds = get_ntp_seconds()
        sdp = [
            'v=0',
            'o=- %d %d IN IP4 0.0.0.0' % (ntp_seconds, ntp_seconds),
            's=-',
            't=0 0',
            'a=fingerprint:sha-256 %s' % self.__dtlsContext.local_fingerprint,
        ]

        for transceiver in self.__transceivers:
            iceConnection = transceiver._iceConnection
            default_candidate = iceConnection.get_default_candidate(1)
            if default_candidate is None:
                default_candidate = DUMMY_CANDIDATE
            sdp += [
                # FIXME: negotiate codec
                'm=audio %d UDP/TLS/RTP/SAVPF 0' % default_candidate.port,
                'c=IN IP4 %s' % default_candidate.host,
                'a=rtcp:9 IN IP4 0.0.0.0',
            ]

            for candidate in iceConnection.local_candidates:
                sdp += ['a=candidate:%s' % candidate.to_sdp()]
            sdp += [
                'a=ice-pwd:%s' % iceConnection.local_password,
                'a=ice-ufrag:%s' % iceConnection.local_username,
            ]
            if iceConnection.ice_controlling:
                sdp += ['a=setup:actpass']
            else:
                sdp += ['a=setup:active']
            sdp += ['a=%s' % transceiver.direction]
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

    def __setSignalingState(self, state):
        self.__signalingState = state
        self.emit('signalingstatechange')
