import asyncio
import copy
import uuid
from collections import OrderedDict
from typing import Dict, List, Optional, Set, Union

from pyee import AsyncIOEventEmitter

from . import clock, rtp, sdp
from .codecs import CODECS, HEADER_EXTENSIONS, is_rtx
from .events import RTCTrackEvent
from .exceptions import InternalError, InvalidAccessError, InvalidStateError
from .mediastreams import MediaStreamTrack
from .rtcconfiguration import RTCConfiguration
from .rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from .rtcdtlstransport import RTCCertificate, RTCDtlsParameters, RTCDtlsTransport
from .rtcicetransport import (
    RTCIceCandidate,
    RTCIceGatherer,
    RTCIceParameters,
    RTCIceTransport,
)
from .rtcrtpparameters import (
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpDecodingParameters,
    RTCRtpHeaderExtensionParameters,
    RTCRtpParameters,
    RTCRtpReceiveParameters,
    RTCRtpRtxParameters,
    RTCRtpSendParameters,
)
from .rtcrtpreceiver import RemoteStreamTrack, RTCRtpReceiver
from .rtcrtpsender import RTCRtpSender
from .rtcrtptransceiver import RTCRtpTransceiver
from .rtcsctptransport import RTCSctpTransport
from .rtcsessiondescription import RTCSessionDescription
from .stats import RTCStatsReport

DISCARD_HOST = "0.0.0.0"
DISCARD_PORT = 9
MEDIA_KINDS = ["audio", "video"]


def filter_preferred_codecs(
    codecs: List[RTCRtpCodecParameters], preferred: List[RTCRtpCodecCapability]
) -> List[RTCRtpCodecParameters]:
    if not preferred:
        return codecs

    rtx_codecs = list(filter(is_rtx, codecs))
    rtx_enabled = next(filter(is_rtx, preferred), None) is not None

    filtered = []
    for pref in filter(lambda x: not is_rtx(x), preferred):
        for codec in codecs:
            if (
                codec.mimeType.lower() == pref.mimeType.lower()
                and codec.parameters == pref.parameters
            ):
                filtered.append(codec)

                # add corresponding RTX
                if rtx_enabled:
                    for rtx in rtx_codecs:
                        if rtx.parameters["apt"] == codec.payloadType:
                            filtered.append(rtx)
                            break

                break

    return filtered


def find_common_codecs(
    local_codecs: List[RTCRtpCodecParameters],
    remote_codecs: List[RTCRtpCodecParameters],
) -> List[RTCRtpCodecParameters]:
    common = []
    common_base: Dict[int, RTCRtpCodecParameters] = {}
    for c in remote_codecs:
        # for RTX, check we accepted the base codec
        if is_rtx(c):
            if c.parameters.get("apt") in common_base:
                base = common_base[c.parameters["apt"]]
                if c.clockRate == base.clockRate:
                    common.append(copy.deepcopy(c))
            continue

        # handle other codecs
        for codec in local_codecs:
            if (
                codec.mimeType.lower() == c.mimeType.lower()
                and codec.clockRate == c.clockRate
            ):
                if codec.mimeType.lower() == "video/h264":
                    # FIXME: check according to RFC 6184
                    parameters_compatible = True
                    for param in ["packetization-mode", "profile-level-id"]:
                        if c.parameters.get(param) != codec.parameters.get(param):
                            parameters_compatible = False
                    if not parameters_compatible:
                        continue

                codec = copy.deepcopy(codec)
                if c.payloadType in rtp.DYNAMIC_PAYLOAD_TYPES:
                    codec.payloadType = c.payloadType
                codec.rtcpFeedback = list(
                    filter(lambda x: x in c.rtcpFeedback, codec.rtcpFeedback)
                )
                common.append(codec)
                common_base[codec.payloadType] = codec
                break
    return common


def find_common_header_extensions(
    local_extensions: List[RTCRtpHeaderExtensionParameters],
    remote_extensions: List[RTCRtpHeaderExtensionParameters],
) -> List[RTCRtpHeaderExtensionParameters]:
    common = []
    for rx in remote_extensions:
        for lx in local_extensions:
            if lx.uri == rx.uri:
                common.append(rx)
    return common


def add_transport_description(
    media: sdp.MediaDescription, dtlsTransport: RTCDtlsTransport
) -> None:
    # ice
    iceTransport = dtlsTransport.transport
    iceGatherer = iceTransport.iceGatherer
    media.ice_candidates = iceGatherer.getLocalCandidates()
    media.ice_candidates_complete = iceGatherer.state == "completed"
    media.ice = iceGatherer.getLocalParameters()
    if media.ice_candidates:
        media.host = media.ice_candidates[0].ip
        media.port = media.ice_candidates[0].port
    else:
        media.host = DISCARD_HOST
        media.port = DISCARD_PORT

    # dtls
    if media.dtls is None:
        media.dtls = dtlsTransport.getLocalParameters()
    else:
        media.dtls.fingerprints = dtlsTransport.getLocalParameters().fingerprints


async def add_remote_candidates(
    iceTransport: RTCIceTransport, media: sdp.MediaDescription
) -> None:
    coros = map(iceTransport.addRemoteCandidate, media.ice_candidates)
    await asyncio.gather(*coros)

    if media.ice_candidates_complete:
        await iceTransport.addRemoteCandidate(None)


def allocate_mid(mids: Set[str]) -> str:
    """
    Allocate a MID which has not been used yet.
    """
    i = 0
    while True:
        mid = str(i)
        if mid not in mids:
            mids.add(mid)
            return mid
        i += 1


def create_media_description_for_sctp(
    sctp: RTCSctpTransport, legacy: bool, mid: str
) -> sdp.MediaDescription:
    if legacy:
        media = sdp.MediaDescription(
            kind="application", port=DISCARD_PORT, profile="DTLS/SCTP", fmt=[sctp.port]
        )
        media.sctpmap[sctp.port] = f"webrtc-datachannel {sctp._outbound_streams_count}"
    else:
        media = sdp.MediaDescription(
            kind="application",
            port=DISCARD_PORT,
            profile="UDP/DTLS/SCTP",
            fmt=["webrtc-datachannel"],
        )
        media.sctp_port = sctp.port

    media.rtp.muxId = mid
    media.sctpCapabilities = sctp.getCapabilities()
    add_transport_description(media, sctp.transport)

    return media


def create_media_description_for_transceiver(
    transceiver: RTCRtpTransceiver, cname: str, direction: str, mid: str
) -> sdp.MediaDescription:
    media = sdp.MediaDescription(
        kind=transceiver.kind,
        port=DISCARD_PORT,
        profile="UDP/TLS/RTP/SAVPF",
        fmt=[c.payloadType for c in transceiver._codecs],
    )
    media.direction = direction
    media.msid = f"{transceiver.sender._stream_id} {transceiver.sender._track_id}"

    media.rtp = RTCRtpParameters(
        codecs=transceiver._codecs,
        headerExtensions=transceiver._headerExtensions,
        muxId=mid,
    )
    media.rtcp_host = DISCARD_HOST
    media.rtcp_port = DISCARD_PORT
    media.rtcp_mux = True
    media.ssrc = [sdp.SsrcDescription(ssrc=transceiver.sender._ssrc, cname=cname)]

    # if RTX is enabled, add corresponding SSRC
    if next(filter(is_rtx, media.rtp.codecs), None):
        media.ssrc.append(
            sdp.SsrcDescription(ssrc=transceiver.sender._rtx_ssrc, cname=cname)
        )
        media.ssrc_group = [
            sdp.GroupDescription(
                semantic="FID",
                items=[transceiver.sender._ssrc, transceiver.sender._rtx_ssrc],
            )
        ]

    add_transport_description(media, transceiver._transport)

    return media


def and_direction(a: str, b: str) -> str:
    return sdp.DIRECTIONS[sdp.DIRECTIONS.index(a) & sdp.DIRECTIONS.index(b)]


def or_direction(a: str, b: str) -> str:
    return sdp.DIRECTIONS[sdp.DIRECTIONS.index(a) | sdp.DIRECTIONS.index(b)]


def reverse_direction(direction: str) -> str:
    if direction == "sendonly":
        return "recvonly"
    elif direction == "recvonly":
        return "sendonly"
    return direction


def wrap_session_description(
    session_description: Optional[sdp.SessionDescription],
) -> Optional[RTCSessionDescription]:
    if session_description is not None:
        return RTCSessionDescription(
            sdp=str(session_description), type=session_description.type
        )
    return None


class RTCPeerConnection(AsyncIOEventEmitter):
    """
    The :class:`RTCPeerConnection` interface represents a WebRTC connection
    between the local computer and a remote peer.

    :param configuration: An optional :class:`RTCConfiguration`.
    """

    def __init__(self, configuration: Optional[RTCConfiguration] = None) -> None:
        super().__init__()
        self.__certificates = [RTCCertificate.generateCertificate()]
        self.__cname = f"{uuid.uuid4()}"
        self.__configuration = configuration or RTCConfiguration()
        self.__iceTransports: Set[RTCIceTransport] = set()
        self.__remoteDtls: Dict[
            Union[RTCRtpTransceiver, RTCSctpTransport], RTCDtlsParameters
        ] = {}
        self.__remoteIce: Dict[
            Union[RTCRtpTransceiver, RTCSctpTransport], RTCIceParameters
        ] = {}
        self.__seenMids: Set[str] = set()
        self.__sctp: Optional[RTCSctpTransport] = None
        self.__sctp_mline_index: Optional[int] = None
        self._sctpLegacySdp = True
        self.__sctpRemotePort: Optional[int] = None
        self.__sctpRemoteCaps = None
        self.__stream_id = str(uuid.uuid4())
        self.__transceivers: List[RTCRtpTransceiver] = []

        self.__iceConnectionState = "new"
        self.__iceGatheringState = "new"
        self.__isClosed = False
        self.__signalingState = "stable"

        self.__currentLocalDescription: Optional[sdp.SessionDescription] = None
        self.__currentRemoteDescription: Optional[sdp.SessionDescription] = None
        self.__pendingLocalDescription: Optional[sdp.SessionDescription] = None
        self.__pendingRemoteDescription: Optional[sdp.SessionDescription] = None

    @property
    def iceConnectionState(self) -> str:
        return self.__iceConnectionState

    @property
    def iceGatheringState(self) -> str:
        return self.__iceGatheringState

    @property
    def localDescription(self) -> RTCSessionDescription:
        """
        An :class:`RTCSessionDescription` describing the session for
        the local end of the connection.
        """
        return wrap_session_description(self.__localDescription())

    @property
    def remoteDescription(self) -> RTCSessionDescription:
        """
        An :class:`RTCSessionDescription` describing the session for
        the remote end of the connection.
        """
        return wrap_session_description(self.__remoteDescription())

    @property
    def sctp(self) -> Optional[RTCSctpTransport]:
        """
        An :class:`RTCSctpTransport` describing the SCTP transport being used
        for datachannels or `None`.
        """
        return self.__sctp

    @property
    def signalingState(self):
        return self.__signalingState

    async def addIceCandidate(self, candidate: RTCIceCandidate) -> None:
        """
        Add a new :class:`RTCIceCandidate` received from the remote peer.

        The specified candidate must have a value for either `sdpMid` or `sdpMLineIndex`.

        :param candidate: The new remote candidate.
        """
        if candidate.sdpMid is None and candidate.sdpMLineIndex is None:
            raise ValueError("Candidate must have either sdpMid or sdpMLineIndex")

        for transceiver in self.__transceivers:
            if candidate.sdpMid == transceiver.mid and not transceiver._bundled:
                iceTransport = transceiver._transport.transport
                await iceTransport.addRemoteCandidate(candidate)
                return

        if (
            self.__sctp
            and candidate.sdpMid == self.__sctp.mid
            and not self.__sctp._bundled
        ):
            iceTransport = self.__sctp.transport.transport
            await iceTransport.addRemoteCandidate(candidate)

    def addTrack(self, track: MediaStreamTrack) -> RTCRtpSender:
        """
        Add a :class:`MediaStreamTrack` to the set of media tracks which
        will be transmitted to the remote peer.
        """
        # check state is valid
        self.__assertNotClosed()
        if track.kind not in ["audio", "video"]:
            raise InternalError(f'Invalid track kind "{track.kind}"')

        # don't add track twice
        self.__assertTrackHasNoSender(track)

        for transceiver in self.__transceivers:
            if transceiver.kind == track.kind:
                if transceiver.sender.track is None:
                    transceiver.sender.replaceTrack(track)
                    transceiver.direction = or_direction(
                        transceiver.direction, "sendonly"
                    )
                    return transceiver.sender

        transceiver = self.__createTransceiver(
            direction="sendrecv", kind=track.kind, sender_track=track
        )
        return transceiver.sender

    def addTransceiver(
        self, trackOrKind: Union[str, MediaStreamTrack], direction: str = "sendrecv"
    ) -> RTCRtpTransceiver:
        """
        Add a new :class:`RTCRtpTransceiver`.
        """
        self.__assertNotClosed()

        # determine track or kind
        if isinstance(trackOrKind, MediaStreamTrack):
            kind = trackOrKind.kind
            track = trackOrKind
        else:
            kind = trackOrKind
            track = None
        if kind not in ["audio", "video"]:
            raise InternalError(f'Invalid track kind "{kind}"')

        # check direction
        if direction not in sdp.DIRECTIONS:
            raise InternalError(f'Invalid direction "{direction}"')

        # don't add track twice
        if track:
            self.__assertTrackHasNoSender(track)

        return self.__createTransceiver(
            direction=direction, kind=kind, sender_track=track
        )

    async def close(self):
        """
        Terminate the ICE agent, ending ICE processing and streams.
        """
        if self.__isClosed:
            return
        self.__isClosed = True
        self.__setSignalingState("closed")

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

        # update states
        self.__updateIceGatheringState()
        self.__updateIceConnectionState()

        # no more events will be emitted, so remove all event listeners
        # to facilitate garbage collection.
        self.remove_all_listeners()

    async def createAnswer(self):
        """
        Create an SDP answer to an offer received from a remote peer during
        the offer/answer negotiation of a WebRTC connection.

        :rtype: :class:`RTCSessionDescription`
        """
        # check state is valid
        self.__assertNotClosed()
        if self.signalingState not in ["have-remote-offer", "have-local-pranswer"]:
            raise InvalidStateError(
                f'Cannot create answer in signaling state "{self.signalingState}"'
            )

        # create description
        ntp_seconds = clock.current_ntp_time() >> 32
        description = sdp.SessionDescription()
        description.origin = f"- {ntp_seconds} {ntp_seconds} IN IP4 0.0.0.0"
        description.msid_semantic.append(
            sdp.GroupDescription(semantic="WMS", items=["*"])
        )
        description.type = "answer"

        for remote_m in self.__remoteDescription().media:
            if remote_m.kind in ["audio", "video"]:
                transceiver = self.__getTransceiverByMid(remote_m.rtp.muxId)
                media = create_media_description_for_transceiver(
                    transceiver,
                    cname=self.__cname,
                    direction=and_direction(
                        transceiver.direction, transceiver._offerDirection
                    ),
                    mid=transceiver.mid,
                )
                dtlsTransport = transceiver._transport
            else:
                media = create_media_description_for_sctp(
                    self.__sctp, legacy=self._sctpLegacySdp, mid=self.__sctp.mid
                )
                dtlsTransport = self.__sctp.transport

            # determine DTLS role, or preserve the currently configured role
            if dtlsTransport._role == "auto":
                media.dtls.role = "client"
            else:
                media.dtls.role = dtlsTransport._role

            description.media.append(media)

        bundle = sdp.GroupDescription(semantic="BUNDLE", items=[])
        for media in description.media:
            bundle.items.append(media.rtp.muxId)
        description.group.append(bundle)

        return wrap_session_description(description)

    def createDataChannel(
        self,
        label,
        maxPacketLifeTime=None,
        maxRetransmits=None,
        ordered=True,
        protocol="",
        negotiated=False,
        id=None,
    ):
        """
        Create a data channel with the given label.

        :rtype: :class:`RTCDataChannel`
        """
        if maxPacketLifeTime is not None and maxRetransmits is not None:
            raise ValueError("Cannot specify both maxPacketLifeTime and maxRetransmits")

        if not self.__sctp:
            self.__createSctpTransport()

        parameters = RTCDataChannelParameters(
            id=id,
            label=label,
            maxPacketLifeTime=maxPacketLifeTime,
            maxRetransmits=maxRetransmits,
            negotiated=negotiated,
            ordered=ordered,
            protocol=protocol,
        )
        return RTCDataChannel(self.__sctp, parameters)

    async def createOffer(self) -> RTCSessionDescription:
        """
        Create an SDP offer for the purpose of starting a new WebRTC
        connection to a remote peer.

        :rtype: :class:`RTCSessionDescription`
        """
        # check state is valid
        self.__assertNotClosed()

        if not self.__sctp and not self.__transceivers:
            raise InternalError(
                "Cannot create an offer with no media and no data channels"
            )

        # offer codecs
        for transceiver in self.__transceivers:
            transceiver._codecs = filter_preferred_codecs(
                CODECS[transceiver.kind][:], transceiver._preferred_codecs
            )
            transceiver._headerExtensions = HEADER_EXTENSIONS[transceiver.kind][:]

        mids = self.__seenMids.copy()

        # create description
        ntp_seconds = clock.current_ntp_time() >> 32
        description = sdp.SessionDescription()
        description.origin = f"- {ntp_seconds} {ntp_seconds} IN IP4 0.0.0.0"
        description.msid_semantic.append(
            sdp.GroupDescription(semantic="WMS", items=["*"])
        )
        description.type = "offer"

        def get_media(
            description: sdp.SessionDescription,
        ) -> List[sdp.MediaDescription]:
            return description.media if description else []

        def get_media_section(
            media: List[sdp.MediaDescription], i: int
        ) -> Optional[sdp.MediaDescription]:
            return media[i] if i < len(media) else None

        # handle existing transceivers / sctp
        local_media = get_media(self.__localDescription())
        remote_media = get_media(self.__remoteDescription())
        for i in range(max(len(local_media), len(remote_media))):
            local_m = get_media_section(local_media, i)
            remote_m = get_media_section(remote_media, i)
            media_kind = local_m.kind if local_m else remote_m.kind
            mid = local_m.rtp.muxId if local_m else remote_m.rtp.muxId
            if media_kind in ["audio", "video"]:
                transceiver = self.__getTransceiverByMid(mid)
                transceiver._set_mline_index(i)
                description.media.append(
                    create_media_description_for_transceiver(
                        transceiver,
                        cname=self.__cname,
                        direction=transceiver.direction,
                        mid=mid,
                    )
                )
            elif media_kind == "application":
                self.__sctp_mline_index = i
                description.media.append(
                    create_media_description_for_sctp(
                        self.__sctp, legacy=self._sctpLegacySdp, mid=mid
                    )
                )

        # handle new transceivers / sctp
        def next_mline_index() -> int:
            return len(description.media)

        for transceiver in filter(
            lambda x: x.mid is None and not x.stopped, self.__transceivers
        ):
            transceiver._set_mline_index(next_mline_index())
            description.media.append(
                create_media_description_for_transceiver(
                    transceiver,
                    cname=self.__cname,
                    direction=transceiver.direction,
                    mid=allocate_mid(mids),
                )
            )
        if self.__sctp and self.__sctp.mid is None:
            self.__sctp_mline_index = next_mline_index()
            description.media.append(
                create_media_description_for_sctp(
                    self.__sctp, legacy=self._sctpLegacySdp, mid=allocate_mid(mids)
                )
            )

        bundle = sdp.GroupDescription(semantic="BUNDLE", items=[])
        for media in description.media:
            bundle.items.append(media.rtp.muxId)
        description.group.append(bundle)

        return wrap_session_description(description)

    def getReceivers(self) -> List[RTCRtpReceiver]:
        """
        Returns the list of :class:`RTCRtpReceiver` objects that are currently
        attached to the connection.
        """
        return list(map(lambda x: x.receiver, self.__transceivers))

    def getSenders(self) -> List[RTCRtpSender]:
        """
        Returns the list of :class:`RTCRtpSender` objects that are currently
        attached to the connection.
        """
        return list(map(lambda x: x.sender, self.__transceivers))

    async def getStats(self) -> RTCStatsReport:
        """
        Returns statistics for the connection.

        :rtype: :class:`RTCStatsReport`
        """
        merged = RTCStatsReport()
        coros = [x.getStats() for x in self.getSenders()] + [
            x.getStats() for x in self.getReceivers()
        ]
        for report in await asyncio.gather(*coros):
            merged.update(report)
        return merged

    def getTransceivers(self) -> List[RTCRtpTransceiver]:
        """
        Returns the list of :class:`RTCRtpTransceiver` objects that are currently
        attached to the connection.
        """
        return list(self.__transceivers)

    async def setLocalDescription(
        self, sessionDescription: RTCSessionDescription
    ) -> None:
        """
        Change the local description associated with the connection.

        :param sessionDescription: An :class:`RTCSessionDescription` generated
                                    by :meth:`createOffer` or :meth:`createAnswer()`.
        """
        # parse and validate description
        description = sdp.SessionDescription.parse(sessionDescription.sdp)
        description.type = sessionDescription.type
        self.__validate_description(description, is_local=True)

        # update signaling state
        if description.type == "offer":
            self.__setSignalingState("have-local-offer")
        elif description.type == "answer":
            self.__setSignalingState("stable")

        # assign MID
        for i, media in enumerate(description.media):
            mid = media.rtp.muxId
            self.__seenMids.add(mid)
            if media.kind in ["audio", "video"]:
                transceiver = self.__getTransceiverByMLineIndex(i)
                transceiver._set_mid(mid)
            elif media.kind == "application":
                self.__sctp.mid = mid

        # set ICE role
        if description.type == "offer":
            for iceTransport in self.__iceTransports:
                if not iceTransport._role_set:
                    iceTransport._connection.ice_controlling = True
                    iceTransport._role_set = True

        # set DTLS role
        if description.type == "answer":
            for i, media in enumerate(description.media):
                if media.kind in ["audio", "video"]:
                    transceiver = self.__getTransceiverByMLineIndex(i)
                    transceiver._transport._set_role(media.dtls.role)
                elif media.kind == "application":
                    self.__sctp.transport._set_role(media.dtls.role)

        # configure direction
        for t in self.__transceivers:
            if description.type in ["answer", "pranswer"]:
                t._currentDirection = and_direction(t.direction, t._offerDirection)

        # gather candidates
        await self.__gather()
        for i, media in enumerate(description.media):
            if media.kind in ["audio", "video"]:
                transceiver = self.__getTransceiverByMLineIndex(i)
                add_transport_description(media, transceiver._transport)
            elif media.kind == "application":
                add_transport_description(media, self.__sctp.transport)

        # connect
        asyncio.ensure_future(self.__connect())

        # replace description
        if description.type == "answer":
            self.__currentLocalDescription = description
            self.__pendingLocalDescription = None
        else:
            self.__pendingLocalDescription = description

    async def setRemoteDescription(
        self, sessionDescription: RTCSessionDescription
    ) -> None:
        """
        Changes the remote description associated with the connection.

        :param sessionDescription: An :class:`RTCSessionDescription` created from
                                    information received over the signaling channel.
        """
        # parse and validate description
        description = sdp.SessionDescription.parse(sessionDescription.sdp)
        description.type = sessionDescription.type
        self.__validate_description(description, is_local=False)

        # apply description
        iceCandidates: Dict[RTCIceTransport, sdp.MediaDescription] = {}
        trackEvents = []
        for i, media in enumerate(description.media):
            dtlsTransport: Optional[RTCDtlsTransport] = None
            self.__seenMids.add(media.rtp.muxId)
            if media.kind in ["audio", "video"]:
                # find transceiver
                transceiver = None
                for t in self.__transceivers:
                    if t.kind == media.kind and t.mid in [None, media.rtp.muxId]:
                        transceiver = t
                if transceiver is None:
                    transceiver = self.__createTransceiver(
                        direction="recvonly", kind=media.kind
                    )
                if transceiver.mid is None:
                    transceiver._set_mid(media.rtp.muxId)
                    transceiver._set_mline_index(i)

                # negotiate codecs
                common = filter_preferred_codecs(
                    find_common_codecs(CODECS[media.kind], media.rtp.codecs),
                    transceiver._preferred_codecs,
                )
                assert len(common)
                transceiver._codecs = common
                transceiver._headerExtensions = find_common_header_extensions(
                    HEADER_EXTENSIONS[media.kind], media.rtp.headerExtensions
                )

                # configure direction
                direction = reverse_direction(media.direction)
                if description.type in ["answer", "pranswer"]:
                    transceiver._currentDirection = direction
                else:
                    transceiver._offerDirection = direction

                # create remote stream track
                if (
                    direction in ["recvonly", "sendrecv"]
                    and not transceiver.receiver.track
                ):
                    transceiver.receiver._track = RemoteStreamTrack(
                        kind=media.kind, id=description.webrtc_track_id(media)
                    )
                    trackEvents.append(
                        RTCTrackEvent(
                            receiver=transceiver.receiver,
                            track=transceiver.receiver.track,
                            transceiver=transceiver,
                        )
                    )

                # memorise transport parameters
                dtlsTransport = transceiver._transport
                self.__remoteDtls[transceiver] = media.dtls
                self.__remoteIce[transceiver] = media.ice

            elif media.kind == "application":
                if not self.__sctp:
                    self.__createSctpTransport()
                if self.__sctp.mid is None:
                    self.__sctp.mid = media.rtp.muxId
                    self.__sctp_mline_index = i

                # configure sctp
                if media.profile == "DTLS/SCTP":
                    self._sctpLegacySdp = True
                    self.__sctpRemotePort = int(media.fmt[0])
                else:
                    self._sctpLegacySdp = False
                    self.__sctpRemotePort = media.sctp_port
                self.__sctpRemoteCaps = media.sctpCapabilities

                # memorise transport parameters
                dtlsTransport = self.__sctp.transport
                self.__remoteDtls[self.__sctp] = media.dtls
                self.__remoteIce[self.__sctp] = media.ice

            if dtlsTransport is not None:
                # add ICE candidates
                iceTransport = dtlsTransport.transport
                iceCandidates[iceTransport] = media

                # set ICE role
                if description.type == "offer" and not iceTransport._role_set:
                    iceTransport._connection.ice_controlling = media.ice.iceLite
                    iceTransport._role_set = True

                # set DTLS role
                if description.type == "answer":
                    dtlsTransport._set_role(
                        role="server" if media.dtls.role == "client" else "client"
                    )

        # remove bundled transports
        bundle = next((x for x in description.group if x.semantic == "BUNDLE"), None)
        if bundle and bundle.items:
            # find main media stream
            masterMid = bundle.items[0]
            masterTransport = None
            for transceiver in self.__transceivers:
                if transceiver.mid == masterMid:
                    masterTransport = transceiver._transport
                    break
            if self.__sctp and self.__sctp.mid == masterMid:
                masterTransport = self.__sctp.transport

            # replace transport for bundled media
            oldTransports = set()
            slaveMids = bundle.items[1:]
            for transceiver in self.__transceivers:
                if transceiver.mid in slaveMids and not transceiver._bundled:
                    oldTransports.add(transceiver._transport)
                    transceiver.receiver.setTransport(masterTransport)
                    transceiver.sender.setTransport(masterTransport)
                    transceiver._bundled = True
                    transceiver._transport = masterTransport
            if (
                self.__sctp
                and self.__sctp.mid in slaveMids
                and not self.__sctp._bundled
            ):
                oldTransports.add(self.__sctp.transport)
                self.__sctp.setTransport(masterTransport)
                self.__sctp._bundled = True

            # stop and discard old ICE transports
            for dtlsTransport in oldTransports:
                await dtlsTransport.stop()
                await dtlsTransport.transport.stop()
                self.__iceTransports.discard(dtlsTransport.transport)
                iceCandidates.pop(dtlsTransport.transport, None)
            self.__updateIceGatheringState()
            self.__updateIceConnectionState()

        # add remote candidates
        coros = [
            add_remote_candidates(iceTransport, media)
            for iceTransport, media in iceCandidates.items()
        ]
        await asyncio.gather(*coros)

        # FIXME: in aiortc 2.0.0 emit RTCTrackEvent directly
        for event in trackEvents:
            self.emit("track", event.track)

        # connect
        asyncio.ensure_future(self.__connect())

        # update signaling state
        if description.type == "offer":
            self.__setSignalingState("have-remote-offer")
        elif description.type == "answer":
            self.__setSignalingState("stable")

        # replace description
        if description.type == "answer":
            self.__currentRemoteDescription = description
            self.__pendingRemoteDescription = None
        else:
            self.__pendingRemoteDescription = description

    async def __connect(self) -> None:
        for transceiver in self.__transceivers:
            dtlsTransport = transceiver._transport
            iceTransport = dtlsTransport.transport
            if (
                iceTransport.iceGatherer.getLocalCandidates()
                and transceiver in self.__remoteIce
            ):
                await iceTransport.start(self.__remoteIce[transceiver])
                if dtlsTransport.state == "new":
                    await dtlsTransport.start(self.__remoteDtls[transceiver])
                if dtlsTransport.state == "connected":
                    if transceiver.currentDirection in ["sendonly", "sendrecv"]:
                        await transceiver.sender.send(self.__localRtp(transceiver))
                    if transceiver.currentDirection in ["recvonly", "sendrecv"]:
                        await transceiver.receiver.receive(
                            self.__remoteRtp(transceiver)
                        )
        if self.__sctp:
            dtlsTransport = self.__sctp.transport
            iceTransport = dtlsTransport.transport
            if (
                iceTransport.iceGatherer.getLocalCandidates()
                and self.__sctp in self.__remoteIce
            ):
                await iceTransport.start(self.__remoteIce[self.__sctp])
                if dtlsTransport.state == "new":
                    await dtlsTransport.start(self.__remoteDtls[self.__sctp])
                if dtlsTransport.state == "connected":
                    await self.__sctp.start(
                        self.__sctpRemoteCaps, self.__sctpRemotePort
                    )

    async def __gather(self) -> None:
        coros = map(lambda t: t.iceGatherer.gather(), self.__iceTransports)
        await asyncio.gather(*coros)

    def __assertNotClosed(self) -> None:
        if self.__isClosed:
            raise InvalidStateError("RTCPeerConnection is closed")

    def __assertTrackHasNoSender(self, track: MediaStreamTrack) -> None:
        for sender in self.getSenders():
            if sender.track == track:
                raise InvalidAccessError("Track already has a sender")

    def __createDtlsTransport(self) -> RTCDtlsTransport:
        # create ICE transport
        iceGatherer = RTCIceGatherer(iceServers=self.__configuration.iceServers)
        iceGatherer.on("statechange", self.__updateIceGatheringState)
        iceTransport = RTCIceTransport(iceGatherer)
        iceTransport.on("statechange", self.__updateIceConnectionState)
        self.__iceTransports.add(iceTransport)

        # update states
        self.__updateIceGatheringState()
        self.__updateIceConnectionState()

        return RTCDtlsTransport(iceTransport, self.__certificates)

    def __createSctpTransport(self) -> None:
        self.__sctp = RTCSctpTransport(self.__createDtlsTransport())
        self.__sctp._bundled = False
        self.__sctp.mid = None

        @self.__sctp.on("datachannel")
        def on_datachannel(channel):
            self.emit("datachannel", channel)

    def __createTransceiver(
        self, direction: str, kind: str, sender_track=None
    ) -> RTCRtpTransceiver:
        dtlsTransport = self.__createDtlsTransport()
        transceiver = RTCRtpTransceiver(
            direction=direction,
            kind=kind,
            sender=RTCRtpSender(sender_track or kind, dtlsTransport),
            receiver=RTCRtpReceiver(kind, dtlsTransport),
        )
        transceiver.receiver._set_rtcp_ssrc(transceiver.sender._ssrc)
        transceiver.sender._stream_id = self.__stream_id
        transceiver._bundled = False
        transceiver._transport = dtlsTransport
        self.__transceivers.append(transceiver)
        return transceiver

    def __getTransceiverByMid(self, mid: str) -> Optional[RTCRtpTransceiver]:
        return next(filter(lambda x: x.mid == mid, self.__transceivers), None)

    def __getTransceiverByMLineIndex(self, index: int) -> Optional[RTCRtpTransceiver]:
        return next(
            filter(lambda x: x._get_mline_index() == index, self.__transceivers), None
        )

    def __localDescription(self) -> Optional[sdp.SessionDescription]:
        return self.__pendingLocalDescription or self.__currentLocalDescription

    def __localRtp(self, transceiver: RTCRtpTransceiver) -> RTCRtpSendParameters:
        rtp = RTCRtpSendParameters(
            codecs=transceiver._codecs,
            headerExtensions=transceiver._headerExtensions,
            muxId=transceiver.mid,
        )
        rtp.rtcp.cname = self.__cname
        rtp.rtcp.ssrc = transceiver.sender._ssrc
        rtp.rtcp.mux = True
        return rtp

    def __remoteDescription(self) -> Optional[sdp.SessionDescription]:
        return self.__pendingRemoteDescription or self.__currentRemoteDescription

    def __remoteRtp(self, transceiver: RTCRtpTransceiver) -> RTCRtpReceiveParameters:
        media = self.__remoteDescription().media[transceiver._get_mline_index()]

        receiveParameters = RTCRtpReceiveParameters(
            codecs=transceiver._codecs,
            headerExtensions=transceiver._headerExtensions,
            muxId=media.rtp.muxId,
            rtcp=media.rtp.rtcp,
        )
        if len(media.ssrc):
            encodings: OrderedDict[int, RTCRtpDecodingParameters] = OrderedDict()
            for codec in transceiver._codecs:
                if is_rtx(codec):
                    if codec.parameters["apt"] in encodings and len(media.ssrc) == 2:
                        encodings[codec.parameters["apt"]].rtx = RTCRtpRtxParameters(
                            ssrc=media.ssrc[1].ssrc
                        )
                    continue

                encodings[codec.payloadType] = RTCRtpDecodingParameters(
                    ssrc=media.ssrc[0].ssrc, payloadType=codec.payloadType
                )
            receiveParameters.encodings = list(encodings.values())
        return receiveParameters

    def __setSignalingState(self, state: str) -> None:
        self.__signalingState = state
        self.emit("signalingstatechange")

    def __updateIceConnectionState(self) -> None:
        # compute new state
        states = set(map(lambda x: x.state, self.__iceTransports))
        if self.__isClosed:
            state = "closed"
        elif "failed" in states:
            state = "failed"
        elif states == set(["completed"]):
            state = "completed"
        elif "checking" in states:
            state = "checking"
        else:
            state = "new"

        # update state
        if state != self.__iceConnectionState:
            self.__iceConnectionState = state
            self.emit("iceconnectionstatechange")

    def __updateIceGatheringState(self) -> None:
        # compute new state
        states = set(map(lambda x: x.iceGatherer.state, self.__iceTransports))
        if states == set(["completed"]):
            state = "complete"
        elif "gathering" in states:
            state = "gathering"
        else:
            state = "new"

        # update state
        if state != self.__iceGatheringState:
            self.__iceGatheringState = state
            self.emit("icegatheringstatechange")

    def __validate_description(
        self, description: sdp.SessionDescription, is_local: bool
    ) -> None:
        # check description is compatible with signaling state
        if is_local:
            if description.type == "offer":
                if self.signalingState not in ["stable", "have-local-offer"]:
                    raise InvalidStateError(
                        f'Cannot handle offer in signaling state "{self.signalingState}"'
                    )
            elif description.type == "answer":
                if self.signalingState not in [
                    "have-remote-offer",
                    "have-local-pranswer",
                ]:
                    raise InvalidStateError(
                        f'Cannot handle answer in signaling state "{self.signalingState}"'
                    )
        else:
            if description.type == "offer":
                if self.signalingState not in ["stable", "have-remote-offer"]:
                    raise InvalidStateError(
                        f'Cannot handle offer in signaling state "{self.signalingState}"'
                    )
            elif description.type == "answer":
                if self.signalingState not in [
                    "have-local-offer",
                    "have-remote-pranswer",
                ]:
                    raise InvalidStateError(
                        f'Cannot handle answer in signaling state "{self.signalingState}"'
                    )

        for media in description.media:
            # check ICE credentials were provided
            if not media.ice.usernameFragment or not media.ice.password:
                raise ValueError("ICE username fragment or password is missing")

            # check DTLS role is allowed
            if description.type == "offer" and media.dtls.role != "auto":
                raise ValueError("DTLS setup attribute must be 'actpass' for an offer")
            if description.type in ["answer", "pranswer"] and media.dtls.role not in [
                "client",
                "server",
            ]:
                raise ValueError(
                    "DTLS setup attribute must be 'active' or 'passive' for an answer"
                )

            # check RTCP mux is used
            if media.kind in ["audio", "video"] and not media.rtcp_mux:
                raise ValueError("RTCP mux is not enabled")

        # check the number of media section matches
        if description.type in ["answer", "pranswer"]:
            offer = (
                self.__remoteDescription() if is_local else self.__localDescription()
            )
            offer_media = [(media.kind, media.rtp.muxId) for media in offer.media]
            answer_media = [
                (media.kind, media.rtp.muxId) for media in description.media
            ]
            if answer_media != offer_media:
                raise ValueError("Media sections in answer do not match offer")
