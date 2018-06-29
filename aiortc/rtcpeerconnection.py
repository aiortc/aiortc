import asyncio
import datetime
import uuid

from pyee import EventEmitter

from . import rtp, sdp
from .codecs import MEDIA_CODECS
from .exceptions import InternalError, InvalidAccessError, InvalidStateError
from .rtcconfiguration import RTCConfiguration
from .rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from .rtcdtlstransport import RTCCertificate, RTCDtlsTransport
from .rtcicetransport import RTCIceCandidate, RTCIceGatherer, RTCIceTransport
from .rtcrtpparameters import RTCRtpParameters
from .rtcrtpreceiver import RemoteStreamTrack, RTCRtpReceiver
from .rtcrtpsender import RTCRtpSender
from .rtcrtptransceiver import RTCRtpTransceiver
from .rtcsctptransport import RTCSctpTransport
from .rtcsessiondescription import RTCSessionDescription

DUMMY_CANDIDATE = RTCIceCandidate(
    foundation='',
    component=1,
    protocol='udp',
    priority=1,
    ip='0.0.0.0',
    port=0,
    type='host')
MEDIA_KINDS = ['audio', 'video']


def find_common_codecs(local_codecs, remote_codecs):
    common = []
    for c in remote_codecs:
        for codec in local_codecs:
            if codec.name == c.name and codec.clockRate == c.clockRate:
                if c.payloadType in rtp.DYNAMIC_PAYLOAD_TYPES:
                    codec = codec.clone(payloadType=c.payloadType)
                common.append(codec)
                break
    return common


def get_ntp_seconds():
    return int((
        datetime.datetime.utcnow() - datetime.datetime(1900, 1, 1, 0, 0, 0)
    ).total_seconds())


def add_transport_description(media, iceTransport, dtlsTransport):
    # ice
    iceGatherer = iceTransport.iceGatherer
    media.ice_candidates = iceGatherer.getLocalCandidates()
    media.ice_candidates_complete = (iceGatherer.state == 'completed')
    media.ice.usernameFragment = iceGatherer.getLocalParameters().usernameFragment
    media.ice.password = iceGatherer.getLocalParameters().password

    # dtls
    dtls_parameters = dtlsTransport.getLocalParameters()
    media.dtls.fingerprints = dtls_parameters.fingerprints
    if iceTransport.role == 'controlling':
        media.dtls.role = 'auto'
    else:
        media.dtls.role = 'client'


def add_remote_candidates(iceTransport, media):
    for candidate in media.ice_candidates:
        iceTransport.addRemoteCandidate(candidate)
    if media.ice_candidates_complete:
        iceTransport.addRemoteCandidate(None)


def get_default_candidate(iceTransport):
    candidates = iceTransport.iceGatherer.getLocalCandidates()
    if candidates:
        return candidates[0]
    else:
        return DUMMY_CANDIDATE


class RTCPeerConnection(EventEmitter):
    """
    The :class:`RTCPeerConnection` interface represents a WebRTC connection
    between the local computer and a remote peer.

    :param: configuration: An optional :class:`RTCConfiguration`.
    """
    def __init__(self, configuration=None):
        super().__init__()
        self.__certificates = [RTCCertificate.generateCertificate()]
        self.__cname = '{%s}' % uuid.uuid4()
        self.__configuration = configuration or RTCConfiguration()
        self.__iceTransports = set()
        self.__initialOfferer = None
        self.__remoteDtls = {}
        self.__remoteIce = {}
        self.__remoteRtp = {}
        self.__sctp = None
        self.__sctpRemotePort = None
        self.__sctpRemoteCaps = None
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
        """
        An :class:`RTCSessionDescription` describing the session for
        the local end of the connection.
        """
        return self.__currentLocalDescription

    @property
    def remoteDescription(self):
        """
        An :class:`RTCSessionDescription` describing the session for
        the remote end of the connection.
        """
        return self.__currentRemoteDescription

    @property
    def sctp(self):
        """
        An :class:`RTCSctpTransport` describing the SCTP transport being used
        for datachannels or `None`.
        """
        return self.__sctp

    @property
    def signalingState(self):
        return self.__signalingState

    def addIceCandidate(self, candidate):
        """
        Add a new :class:`RTCIceCandidate` received from the remote peer.

        The specified candidate must have a value for both `sdpMid` and `sdpMLineIndex`.
        """
        if candidate.sdpMid is None or candidate.sdpMLineIndex is None:
            raise ValueError('Candidate must have both sdpMid and sdpMLineIndex')

        for transceiver in self.__transceivers:
            if candidate.sdpMid == transceiver.mid and not transceiver._bundled:
                iceTransport = transceiver._transport.transport
                iceTransport.addRemoteCandidate(candidate)
                return

        if self.__sctp and candidate.sdpMid == self.__sctp.mid and not self.__sctp._bundled:
            iceTransport = self.__sctp.transport.transport
            iceTransport.addRemoteCandidate(candidate)

    def addTrack(self, track):
        """
        Add a :class:`MediaStreamTrack` to the set of media tracks which
        will be transmitted to the remote peer.
        """
        # check state is valid
        self.__assertNotClosed()
        if track.kind not in ['audio', 'video']:
            raise InternalError('Invalid track kind "%s"' % track.kind)

        # don't add track twice
        for sender in self.getSenders():
            if sender.track == track:
                raise InvalidAccessError('Track already has a sender')

        for transceiver in self.__transceivers:
            if transceiver.kind == track.kind:
                if transceiver.sender.track is None:
                    transceiver.sender.replaceTrack(track)
                    return transceiver.sender
                else:
                    raise InternalError('Only a single %s track is supported for now' % track.kind)

        transceiver = self.__createTransceiver(kind=track.kind, sender_track=track)
        transceiver.mid = track.kind
        return transceiver.sender

    async def close(self):
        """
        Terminate the ICE agent, ending ICE processing and streams.
        """
        if self.__isClosed:
            return
        self.__isClosed = True
        self.__setSignalingState('closed')
        self.__updateIceConnectionState()

        # stop senders / receivers
        for transceiver in self.__transceivers:
            await transceiver.stop()
        if self.__sctp:
            await self.__sctp.stop()

        # stop transports
        for transceiver in self.__transceivers:
            await transceiver._transport.stop()
            await transceiver._transport.transport.stop()
        if self.__sctp:
            await self.__sctp.transport.stop()
            await self.__sctp.transport.transport.stop()

    async def createAnswer(self):
        """
        Create an SDP answer to an offer received from a remote peer during
        the offer/answer negotiation of a WebRTC connection.

        :rtype: :class:`RTCSessionDescription`
        """
        # check state is valid
        self.__assertNotClosed()
        if self.signalingState not in ['have-remote-offer', 'have-local-pranswer']:
            raise InvalidStateError('Cannot create answer in signaling state "%s"' %
                                    self.signalingState)

        return RTCSessionDescription(
            sdp=self.__createSdp(),
            type='answer')

    def createDataChannel(self, label, protocol=''):
        """
        Create a data channel with the given label.

        :rtype: :class:`RTCDataChannel`
        """
        if not self.__sctp:
            self.__createSctpTransport()
            self.__sctp.mid = 'data'

        parameters = RTCDataChannelParameters(label=label, protocol=protocol)
        return RTCDataChannel(self.__sctp, parameters)

    async def createOffer(self):
        """
        Create an SDP offer for the purpose of starting a new WebRTC
        connection to a remote peer.

        :rtype: :class:`RTCSessionDescription`
        """
        # check state is valid
        self.__assertNotClosed()

        if not self.__sctp and not self.__transceivers:
            raise InternalError('Cannot create an offer with no media and no data channels')

        # offer codecs
        dynamic_pt = rtp.DYNAMIC_PAYLOAD_TYPES.start
        for transceiver in self.__transceivers:
            codecs = []
            for codec in MEDIA_CODECS[transceiver.kind]:
                if codec.payloadType is None:
                    codecs.append(codec.clone(payloadType=dynamic_pt))
                    dynamic_pt += 1
                else:
                    codecs.append(codec)
            transceiver._codecs = codecs

        return RTCSessionDescription(
            sdp=self.__createSdp(),
            type='offer')

    def getReceivers(self):
        """
        Returns the list of :class:`RTCRtpReceiver` objects that are currently
        attached to the connection.
        """
        return list(map(lambda x: x.receiver, self.__transceivers))

    def getSenders(self):
        """
        Returns the list of :class:`RTCRtpSender` objects that are currently
        attached to the connection.
        """
        return list(map(lambda x: x.sender, self.__transceivers))

    def getTransceivers(self):
        """
        Returns the list of :class:`RTCRtpTransceiver` objects that are currently
        attached to the connection.
        """
        return list(self.__transceivers)

    async def setLocalDescription(self, sessionDescription):
        """
        Change the local description associated with the connection.

        :param: sessionDescription: An :class:`RTCSessionDescription` generated
                                    by :meth:`createOffer` or :meth:`createAnswer()`.
        """
        if sessionDescription.type == 'offer':
            self.__setSignalingState('have-local-offer')
        elif sessionDescription.type == 'answer':
            self.__setSignalingState('stable')

        # set ICE role
        if self.__initialOfferer is None:
            self.__initialOfferer = (sessionDescription.type == 'offer')
            for iceTransport in self.__iceTransports:
                iceTransport._connection.ice_controlling = self.__initialOfferer

        # gather
        await self.__gather()

        # connect
        asyncio.ensure_future(self.__connect())

        self.__currentLocalDescription = RTCSessionDescription(
            sdp=self.__createSdp(),
            type=sessionDescription.type)

    async def setRemoteDescription(self, sessionDescription):
        """
        Changes the remote description associated with the connection.

        :param: sessionDescription: An :class:`RTCSessionDescription` created from
                                    information received over the signaling channel.
        """
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
            if media.kind in ['audio', 'video']:
                # find transceiver
                transceiver = None
                for t in self.__transceivers:
                    if t.kind == media.kind:
                        transceiver = t
                if transceiver is None:
                    transceiver = self.__createTransceiver(kind=media.kind)
                if not self.__initialOfferer:
                    transceiver.mid = media.rtp.muxId

                # negotiate codecs
                common = find_common_codecs(MEDIA_CODECS[media.kind], media.rtp.codecs)
                assert len(common)
                transceiver._codecs = common

                # configure transport
                iceTransport = transceiver._transport.transport
                add_remote_candidates(iceTransport, media)
                self.__remoteDtls[transceiver] = media.dtls
                self.__remoteIce[transceiver] = media.ice
                self.__remoteRtp[transceiver] = media.rtp

                if not transceiver.receiver._track:
                    transceiver.receiver._track = RemoteStreamTrack(kind=media.kind)
                    self.emit('track', transceiver.receiver._track)

            elif media.kind == 'application':
                if not self.__sctp:
                    self.__createSctpTransport()
                if not self.__initialOfferer:
                    self.__sctp.mid = media.rtp.muxId

                # configure sctp
                self.__sctpRemotePort = media.fmt[0]
                self.__sctpRemoteCaps = media.sctpCapabilities

                # configure transport
                iceTransport = self.__sctp.transport.transport
                add_remote_candidates(iceTransport, media)
                self.__remoteDtls[self.__sctp] = media.dtls
                self.__remoteIce[self.__sctp] = media.ice

        # remove bundled transports
        if parsedRemoteDescription.bundle:
            # find main media stream
            masterMid = parsedRemoteDescription.bundle[0]
            masterTransport = None
            for transceiver in self.__transceivers:
                if transceiver.mid == masterMid:
                    masterTransport = transceiver._transport
                    break
            if self.__sctp and self.__sctp.mid == masterMid:
                masterTransport = self.__sctp.transport

            # replace transport for bundled media
            oldTransports = set()
            slaveMids = parsedRemoteDescription.bundle[1:]
            for transceiver in self.__transceivers:
                if transceiver.mid in slaveMids and not transceiver._bundled:
                    oldTransports.add(transceiver._transport)
                    transceiver.receiver.setTransport(masterTransport)
                    transceiver.sender.setTransport(masterTransport)
                    transceiver._bundled = True
                    transceiver._transport = masterTransport
            if self.__sctp and self.__sctp.mid in slaveMids:
                oldTransports.add(self.__sctp.transport)
                self.__sctp.setTransport(masterTransport)
                self.__sctp._bundled = True

            # discard old ICE transports
            oldIceTransports = set([x.transport for x in oldTransports])
            self.__iceTransports = list(filter(
                lambda x: x not in oldIceTransports, self.__iceTransports))

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
            dtlsTransport = transceiver._transport
            iceTransport = dtlsTransport.transport
            if iceTransport.iceGatherer.getLocalCandidates() and transceiver in self.__remoteIce:
                await iceTransport.start(self.__remoteIce[transceiver])
                await dtlsTransport.start(self.__remoteDtls[transceiver])
                await transceiver.sender.send(RTCRtpParameters(codecs=transceiver._codecs))
                await transceiver.receiver.receive(self.__remoteRtp[transceiver])
        if self.__sctp:
            dtlsTransport = self.__sctp.transport
            iceTransport = dtlsTransport.transport
            if iceTransport.iceGatherer.getLocalCandidates() and self.__sctp in self.__remoteIce:
                await iceTransport.start(self.__remoteIce[self.__sctp])
                await dtlsTransport.start(self.__remoteDtls[self.__sctp])
                self.__sctp.start(self.__sctpRemoteCaps, self.__sctpRemotePort)

    async def __gather(self):
        coros = map(lambda t: t.iceGatherer.gather(), self.__iceTransports)
        await asyncio.gather(*coros)

    def __assertNotClosed(self):
        if self.__isClosed:
            raise InvalidStateError('RTCPeerConnection is closed')

    def __createDtlsTransport(self):
        # create ICE transport
        iceGatherer = RTCIceGatherer(iceServers=self.__configuration.iceServers)
        iceGatherer.on('statechange', self.__updateIceGatheringState)
        iceTransport = RTCIceTransport(iceGatherer)
        iceTransport.on('statechange', self.__updateIceConnectionState)
        self.__iceTransports.add(iceTransport)

        # update states
        self.__updateIceGatheringState()
        self.__updateIceConnectionState()

        return RTCDtlsTransport(iceTransport, self.__certificates)

    def __createSctpTransport(self):
        self.__sctp = RTCSctpTransport(self.__createDtlsTransport())
        self.__sctp._bundled = False

        @self.__sctp.on('datachannel')
        def on_datachannel(channel):
            self.emit('datachannel', channel)

    def __createSdp(self):
        ntp_seconds = get_ntp_seconds()
        description = sdp.SessionDescription()
        description.origin = '- %d %d IN IP4 0.0.0.0' % (ntp_seconds, ntp_seconds)

        for transceiver in self.__transceivers:
            dtlsTransport = transceiver._transport
            iceTransport = dtlsTransport.transport
            default_candidate = get_default_candidate(iceTransport)

            media = sdp.MediaDescription(
                kind=transceiver.kind,
                port=default_candidate.port,
                profile='UDP/TLS/RTP/SAVPF',
                fmt=[c.payloadType for c in transceiver._codecs])
            media.host = default_candidate.ip
            media.direction = transceiver.direction
            media.rtp.codecs = transceiver._codecs
            media.rtp.muxId = transceiver.mid
            media.rtp.rtcp.cname = self.__cname
            media.rtp.rtcp.ssrc = transceiver.sender._ssrc
            media.rtp.rtcp.mux = True
            media.rtcp_host = '0.0.0.0'
            media.rtcp_port = 9
            add_transport_description(media, iceTransport, dtlsTransport)

            description.media.append(media)
            description.bundle.append(media.rtp.muxId)

        if self.__sctp:
            dtlsTransport = self.__sctp.transport
            iceTransport = dtlsTransport.transport
            default_candidate = get_default_candidate(iceTransport)

            media = sdp.MediaDescription(
                kind='application',
                port=default_candidate.port,
                profile='DTLS/SCTP',
                fmt=[self.__sctp.port])
            media.host = default_candidate.ip
            media.rtp.muxId = self.__sctp.mid
            media.sctpmap[self.__sctp.port] = 'webrtc-datachannel %d' % self.__sctp.outbound_streams
            media.sctpCapabilities = self.__sctp.getCapabilities()
            add_transport_description(media, iceTransport, dtlsTransport)

            description.media.append(media)
            description.bundle.append(media.rtp.muxId)

        return str(description)

    def __createTransceiver(self, kind, sender_track=None):
        dtlsTransport = self.__createDtlsTransport()
        transceiver = RTCRtpTransceiver(
            kind=kind,
            sender=RTCRtpSender(sender_track or kind, dtlsTransport),
            receiver=RTCRtpReceiver(kind, dtlsTransport))
        transceiver.receiver._ssrc = transceiver.sender._ssrc
        transceiver.sender._cname = self.__cname
        transceiver._bundled = False
        transceiver._transport = dtlsTransport
        self.__transceivers.append(transceiver)
        return transceiver

    def __setSignalingState(self, state):
        self.__signalingState = state
        self.emit('signalingstatechange')

    def __updateIceConnectionState(self):
        # compute new state
        states = set(map(lambda x: x.state, self.__iceTransports))
        if self.__isClosed:
            state = 'closed'
        elif states == set(['completed']):
            state = 'completed'
        elif 'checking' in states:
            state = 'checking'
        else:
            state = 'new'

        # update state
        if state != self.__iceConnectionState:
            self.__iceConnectionState = state
            self.emit('iceconnectionstatechange')

    def __updateIceGatheringState(self):
        # compute new state
        states = set(map(lambda x: x.iceGatherer.state, self.__iceTransports))
        if states == set(['completed']):
            state = 'complete'
        elif 'gathering' in states:
            state = 'gathering'
        else:
            state = 'new'

        # update state
        if state != self.__iceGatheringState:
            self.__iceGatheringState = state
            self.emit('icegatheringstatechange')
