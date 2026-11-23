import asyncio
import copy
import logging
import uuid
from typing import Optional, Union

from pyee.asyncio import AsyncIOEventEmitter

from . import clock, rtp, sdp
from .codecs import CODECS, HEADER_EXTENSIONS, is_rtx
from .events import RTCTrackEvent
from .exceptions import (
    InternalError,
    InvalidAccessError,
    InvalidStateError,
    OperationError,
)
from .mediastreams import MediaStreamTrack
from .rtcconfiguration import RTCBundlePolicy, RTCConfiguration
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
from .rtcsctptransport import RTCSctpCapabilities, RTCSctpTransport
from .rtcsessiondescription import RTCSessionDescription
from .stats import RTCStatsReport

DISCARD_HOST = "0.0.0.0"
DISCARD_PORT = 9
MEDIA_KINDS = ["audio", "video"]

logger = logging.getLogger(__name__)


def can_start_dtls(dtlsTransport: RTCDtlsTransport) -> bool:
    """
    Return whether the DTLS handshake can start.

    A handshake runs once and needs the connection found by ICE, so DTLS must be
    in "new" and ICE in "completed". If ICE fails, DTLS stays in "new" as it has
    not started negotiating (W3C WebRTC section 5.5.1).
    """
    return dtlsTransport.state == "new" and dtlsTransport.transport.state == "completed"


def can_start_ice(iceTransport: RTCIceTransport) -> bool:
    """
    Return whether ICE can start.

    The checks use the gathered candidates, so gathering must have completed, and
    a closed transport cannot be started. If no candidates were gathered, ICE
    goes on to fail once the remote candidates end (W3C WebRTC section 5.6.4).
    """
    return (
        iceTransport.iceGatherer.state == "completed" and iceTransport.state != "closed"
    )


def filter_preferred_codecs(
    codecs: list[RTCRtpCodecParameters], preferred: list[RTCRtpCodecCapability]
) -> list[RTCRtpCodecParameters]:
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
    local_codecs: list[RTCRtpCodecParameters],
    remote_codecs: list[RTCRtpCodecParameters],
) -> list[RTCRtpCodecParameters]:
    # Match codecs other than RTX first, as only "apt" ties an RTX codec to the
    # codec it repairs, which may be listed after it (RFC 4588 section 8.1).
    matched: dict[int, RTCRtpCodecParameters] = {}
    for index, c in enumerate(remote_codecs):
        if is_rtx(c):
            continue

        for codec in local_codecs:
            if is_codec_compatible(codec, c):
                codec = copy.deepcopy(codec)
                if c.payloadType in rtp.DYNAMIC_PAYLOAD_TYPES:
                    codec.payloadType = c.payloadType
                codec.rtcpFeedback = list(
                    filter(lambda x: x in c.rtcpFeedback, codec.rtcpFeedback)
                )
                matched[index] = codec
                break

    # Then list the codecs in the remote order, keeping an RTX codec only if
    # the codec it repairs was matched.
    common_base = {codec.payloadType: codec for codec in matched.values()}
    common = []
    for index, c in enumerate(remote_codecs):
        if index in matched:
            common.append(matched[index])
        elif is_rtx(c):
            apt = c.parameters.get("apt")
            if isinstance(apt, int) and apt in common_base:
                base = common_base[apt]
                if c.clockRate == base.clockRate:
                    common.append(copy.deepcopy(c))
    return common


def find_common_header_extensions(
    local_extensions: list[RTCRtpHeaderExtensionParameters],
    remote_extensions: list[sdp.ExtmapDescription],
) -> list[RTCRtpHeaderExtensionParameters]:
    common: list[RTCRtpHeaderExtensionParameters] = []
    for rx in remote_extensions:
        # Without attributes, a URI must not appear twice (RFC 8285 section 5).
        if any(x.uri == rx.uri for x in common):
            continue
        for lx in local_extensions:
            if lx.uri == rx.uri:
                common.append(RTCRtpHeaderExtensionParameters(id=rx.id, uri=rx.uri))
    return common


def is_codec_compatible(a: RTCRtpCodecParameters, b: RTCRtpCodecParameters) -> bool:
    if a.mimeType.lower() != b.mimeType.lower() or a.clockRate != b.clockRate:
        return False

    if a.mimeType.lower() == "video/h264":

        def packetization(c: RTCRtpCodecParameters) -> int:
            return int(c.parameters.get("packetization-mode", "0"))

        def profile(c: RTCRtpCodecParameters) -> sdp.H264Profile:
            # for backwards compatibility with older versions of WebRTC,
            # consider the absence of a profile-level-id parameter to mean
            # "constrained baseline level 3.1"
            return sdp.parse_h264_profile_level_id(
                str(c.parameters.get("profile-level-id", "42E01F"))
            )[0]

        try:
            return packetization(a) == packetization(b) and profile(a) == profile(b)
        except ValueError:
            return False

    return True


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


def allocate_mid(mids: set[str]) -> str:
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
    media.extmap = [
        sdp.ExtmapDescription(id=x.id, uri=x.uri) for x in transceiver._headerExtensions
    ]
    media.msid = f"{transceiver.sender._stream_id} {transceiver.sender._track_id}"

    media.rtp = RTCRtpParameters(codecs=transceiver._codecs, muxId=mid)
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

    add_transport_description(media, transceiver.receiver.transport)

    return media


def add_bundle_group(description: sdp.SessionDescription, mids: list[str]) -> None:
    """
    Add a BUNDLE group listing those of the given MIDs whose "m=" sections are
    not rejected (RFC 9429 sections 5.2.2 and 5.3.1).
    """
    accepted = [m.rtp.muxId for m in description.media if not is_rejected(m)]
    items: list[Union[int, str]] = [mid for mid in mids if mid in accepted]
    if items:
        description.group.append(sdp.GroupDescription(semantic="BUNDLE", items=items))


def get_bundle_mids(description: Optional[sdp.SessionDescription]) -> list[str]:
    """
    Return the MIDs in the BUNDLE group, the first being the BUNDLE tag
    (RFC 9143 section 7.5).
    """
    if description is not None:
        for group in description.group:
            if group.semantic == "BUNDLE":
                return [str(item) for item in group.items]
    return []


def get_bundle_tag(description: Optional[sdp.SessionDescription]) -> Optional[str]:
    """
    Return the MID of the BUNDLE-tagged "m=" section, if there is a BUNDLE group.
    """
    mids = get_bundle_mids(description)
    return mids[0] if mids else None


def is_rejected(media: sdp.MediaDescription) -> bool:
    """
    Return whether an "m=" section is rejected, which its zero port indicates
    unless it is bundle-only (RFC 9143 section 6).
    """
    return media.port == 0 and not media.bundle_only


def reject_stopped_media(
    description: sdp.SessionDescription,
    stopped: list[tuple[sdp.MediaDescription, sdp.MediaDescription]],
    bundle_tag: Optional[str],
) -> None:
    """
    Reject the "m=" sections of stopped transceivers, each given with the
    offered "m=" section (RFC 9429 sections 5.2.2 and 5.3.1).

    A rejected "m=" section has a zero port and no "a=msid" line. Its transport
    attributes are kept, as earlier versions of aiortc require them.

    Rejecting the negotiated BUNDLE-tagged "m=" section, whose transport is in
    use, moves browsers to a new transport aiortc cannot follow, so it is only
    made inactive while others remain.
    """
    stopped_media = [media for media, offered in stopped]
    others_remain = any(
        m.rtp.muxId != bundle_tag and not is_rejected(m) and m not in stopped_media
        for m in description.media
    )
    for media, offered in stopped:
        media.direction = "inactive"
        if media.rtp.muxId == bundle_tag and others_remain:
            continue

        media.port = 0
        media.host = DISCARD_HOST
        media.extmap = []
        media.msid = None
        media.ssrc = []
        media.ssrc_group = []
        media.ice_candidates = []
        media.ice_candidates_complete = False

        # The format is ignored, but at least one is required, which may be
        # the first offered one (RFC 3264 sections 6 and 8.2).
        media.fmt = offered.fmt[:1]
        media.rtp = RTCRtpParameters(
            codecs=[c for c in offered.rtp.codecs if c.payloadType in media.fmt],
            muxId=media.rtp.muxId,
        )


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
    :param certificate: An optional :class:`RTCCertificate`.
    """

    def __init__(
            self, configuration: Optional[RTCConfiguration] = None, certificate: Optional[RTCCertificate] = None
    ) -> None:
        super().__init__()
        self.__certificates = [certificate or RTCCertificate.generateCertificate()]
        self.__cname = f"{uuid.uuid4()}"
        self.__configuration = configuration or RTCConfiguration()
        self.__dtlsTransports: set[RTCDtlsTransport] = set()
        self.__iceTransports: set[RTCIceTransport] = set()
        self.__remoteDtls: dict[
            Union[RTCRtpTransceiver, RTCSctpTransport], RTCDtlsParameters
        ] = {}
        self.__remoteIce: dict[
            Union[RTCRtpTransceiver, RTCSctpTransport], RTCIceParameters
        ] = {}
        self.__seenMids: set[str] = set()
        self.__sctp: Optional[RTCSctpTransport] = None
        self.__sctp_mline_index: Optional[int] = None
        # By default we use "UDP/DTLS/SCTP" in our SDP, but we also
        # accept "DTLS/SCTP" from older browsers.
        self._sctpLegacySdp = False
        self.__sctpRemotePort: Optional[int] = None
        self.__sctpRemoteCaps: Optional[RTCSctpCapabilities] = None
        self.__stream_id = str(uuid.uuid4())
        self.__transceivers: list[RTCRtpTransceiver] = []

        self.__closeTask: Optional[asyncio.Task] = None
        self.__connectionState = "new"
        self.__iceConnectionState = "new"
        self.__iceGatheringState = "new"
        self.__isClosed: Optional[asyncio.Future[bool]] = None
        self.__signalingState = "stable"

        self.__currentLocalDescription: Optional[sdp.SessionDescription] = None
        self.__currentRemoteDescription: Optional[sdp.SessionDescription] = None
        self.__pendingLocalDescription: Optional[sdp.SessionDescription] = None
        self.__pendingRemoteDescription: Optional[sdp.SessionDescription] = None

        # The BUNDLE tag of the last answer, whose transport the group uses.
        self.__negotiatedBundleTag: Optional[str] = None

    @property
    def connectionState(self) -> str:
        """
        The current connection state.

        Possible values: `"connected"`, `"connecting"`, `"closed"`, `"failed"`, `"new`".

        When the state changes, the `"connectionstatechange"` event is fired.
        """
        return self.__connectionState

    @property
    def iceConnectionState(self) -> str:
        """
        The current ICE connection state.

        Possible values: `"checking"`, `"completed"`, `"closed"`, `"failed"`, `"new`".

        When the state changes, the `"iceconnectionstatechange"` event is fired.
        """
        return self.__iceConnectionState

    @property
    def iceGatheringState(self) -> str:
        """
        The current ICE gathering state.

        Possible values: `"complete"`, `"gathering"`, `"new`".

        When the state changes, the `"icegatheringstatechange"` event is fired.
        """
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
    def signalingState(self) -> str:
        """
        The current signaling state.

        Possible values: `"closed"`, `"have-local-offer"`, `"have-remote-offer`",
        `"stable"`.

        When the state changes, the `"signalingstatechange"` event is fired.
        """
        return self.__signalingState

    async def addIceCandidate(self, candidate: Optional[RTCIceCandidate]) -> None:
        """
        Add a new :class:`RTCIceCandidate` received from the remote peer.

        The specified candidate must have a value for either `sdpMid` or
        `sdpMLineIndex`.

        :param candidate: The new remote candidate or `None` to signal
                            end-of-candidates.
        """
        if (
            candidate is not None
            and candidate.sdpMid is None
            and candidate.sdpMLineIndex is None
        ):
            raise ValueError("Candidate must have either sdpMid or sdpMLineIndex")

        for transceiver in self.__transceivers:
            if (
                candidate is None
                or (
                    candidate.sdpMid == transceiver.mid
                    or candidate.sdpMLineIndex == transceiver._get_mline_index()
                )
                and not transceiver._bundled
            ):
                iceTransport = transceiver.receiver.transport.transport
                await iceTransport.addRemoteCandidate(candidate)

        if self.__sctp and (
            candidate is None
            or (
                candidate.sdpMid == self.__sctp.mid
                or candidate.sdpMLineIndex == self.__sctp_mline_index
            )
            and not self.__sctp._bundled
        ):
            iceTransport = self.__sctp.transport.transport
            await iceTransport.addRemoteCandidate(candidate)

        # Update the remote description if it exists.
        # The spec forbids adding candidates prior to setRemoteDescription
        # but this used to work in previous versions.
        if remote_description := self.__remoteDescription():
            media = remote_description.media
            for sdp_m_line_index in range(0, len(media)):
                if candidate is None:
                    media[sdp_m_line_index].ice_candidates_complete = True
                elif (
                    candidate.sdpMLineIndex == sdp_m_line_index
                    or candidate.sdpMid == media[sdp_m_line_index].rtp.muxId
                ):
                    media[sdp_m_line_index].ice_candidates.append(candidate)
        else:
            logger.warning(
                "RTCPeerConnection addIceCandidate called without remote description"
            )

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

        # Reuse a transceiver without a track, unless it is stopped (W3C
        # webrtc-pc, addTrack).
        for transceiver in self.__transceivers:
            if (
                transceiver.kind == track.kind
                and transceiver.sender.track is None
                and not transceiver.stopped
            ):
                transceiver.sender.replaceTrack(track)
                transceiver.direction = or_direction(transceiver.direction, "sendonly")
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

    async def close(self) -> None:
        """
        Terminate the ICE agent, ending ICE processing and streams.
        """
        if self.__isClosed:
            await self.__isClosed
            return
        self.__isClosed = asyncio.Future()
        self.__setSignalingState("closed")

        # stop senders / receivers
        for transceiver in self.__transceivers:
            await transceiver.stop()
        if self.__sctp:
            await self.__sctp.stop()

        # stop transports
        for transceiver in self.__transceivers:
            await transceiver.receiver.transport.stop()
            await transceiver.receiver.transport.transport.stop()
        if self.__sctp:
            await self.__sctp.transport.stop()
            await self.__sctp.transport.transport.stop()

        # update states
        self.__updateIceGatheringState()
        self.__updateIceConnectionState()
        self.__updateConnectionState()

        # no more events will be emitted, so remove all event listeners
        # to facilitate garbage collection.
        self.remove_all_listeners()

        self.__isClosed.set_result(True)

    async def createAnswer(self) -> RTCSessionDescription:
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

        remote = self.__remoteDescription()
        stopped = []
        for remote_m in remote.media:
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
                if transceiver.stopped:
                    stopped.append((media, remote_m))
                dtlsTransport = transceiver.receiver.transport
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

        reject_stopped_media(
            description, stopped, bundle_tag=self.__negotiatedBundleTag
        )
        add_bundle_group(description, get_bundle_mids(remote))

        return wrap_session_description(description)

    def createDataChannel(
        self,
        label: str,
        maxPacketLifeTime: Optional[int] = None,
        maxRetransmits: Optional[int] = None,
        ordered: bool = True,
        protocol: str = "",
        negotiated: bool = False,
        id: Optional[int] = None,
    ) -> RTCDataChannel:
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
        ) -> list[sdp.MediaDescription]:
            return description.media if description else []

        def get_media_section(
            media: list[sdp.MediaDescription], i: int
        ) -> Optional[sdp.MediaDescription]:
            return media[i] if i < len(media) else None

        # handle existing transceivers / sctp
        stopped = []
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
                media = create_media_description_for_transceiver(
                    transceiver,
                    cname=self.__cname,
                    direction=transceiver.direction,
                    mid=mid,
                )
                if transceiver.stopped:
                    stopped.append((media, media))
                description.media.append(media)
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

        def add_datachannel_section() -> None:
            self.__sctp_mline_index = next_mline_index()
            description.media.append(
                create_media_description_for_sctp(
                    self.__sctp, legacy=self._sctpLegacySdp, mid=allocate_mid(mids)
                )
            )

        # JSEP specifies that it should only be added after audio/video m-sections
        # but the `alwaysNegotiateDataChannels` option overrides that:
        # https://w3c.github.io/webrtc-extensions/#always-negotiating-datachannels
        # This implies that it will be used for negotiating BUNDLE.
        if not self.__sctp and self.__configuration.alwaysNegotiateDataChannels:
            self.__createSctpTransport()
            add_datachannel_section()

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
        if (
            self.__sctp
            and self.__sctp.mid is None
            and not self.__configuration.alwaysNegotiateDataChannels
        ):
            add_datachannel_section()

        reject_stopped_media(
            description, stopped, bundle_tag=self.__negotiatedBundleTag
        )
        add_bundle_group(description, [m.rtp.muxId for m in description.media])

        return wrap_session_description(description)

    def getReceivers(self) -> list[RTCRtpReceiver]:
        """
        Returns the list of :class:`RTCRtpReceiver` objects that are currently
        attached to the connection.
        """
        return list(map(lambda x: x.receiver, self.__transceivers))

    def getSenders(self) -> list[RTCRtpSender]:
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

    def getTransceivers(self) -> list[RTCRtpTransceiver]:
        """
        Returns the list of :class:`RTCRtpTransceiver` objects that are currently
        attached to the connection.
        """
        return list(self.__transceivers)

    async def setLocalDescription(
        self, sessionDescription: Optional[RTCSessionDescription] = None
    ) -> None:
        """
        Change the local description associated with the connection.

        :param sessionDescription: An :class:`RTCSessionDescription` generated
                                    by :meth:`createOffer` or :meth:`createAnswer()`
                                    or `None` to implicitly create an offer or create
                                    an answer, as needed.
        """
        # check state is valid
        self.__assertNotClosed()

        if sessionDescription is None:
            # https://w3c.github.io/webrtc-pc/#dom-peerconnection-setlocaldescription
            # If left out, then setLocalDescription will implicitly create an offer
            # or create an answer, as needed.
            if self.signalingState == "have-remote-offer":
                sessionDescription = await self.createAnswer()
            else:
                sessionDescription = await self.createOffer()
            self.__log_debug(
                "setLocalDescription(%s, implicit)\n%s",
                sessionDescription.type,
                sessionDescription.sdp,
            )
        else:
            self.__log_debug(
                "setLocalDescription(%s)\n%s",
                sessionDescription.type,
                sessionDescription.sdp,
            )

        # Parse and validate the description.
        description = sdp.SessionDescription.parse(sessionDescription.sdp)
        description.type = sessionDescription.type
        self.__validate_description(description, is_local=True)

        # Assign the MIDs.
        for i, media in enumerate(description.media):
            mid = media.rtp.muxId
            self.__seenMids.add(mid)
            if media.kind in ["audio", "video"]:
                transceiver = self.__getTransceiverByMLineIndex(i)
                transceiver._set_mid(mid)
            elif media.kind == "application":
                self.__sctp.mid = mid

        # Set the ICE role.
        if description.type == "offer":
            for iceTransport in self.__iceTransports:
                if not iceTransport._role_set:
                    iceTransport._connection.ice_controlling = True
                    iceTransport._role_set = True

        # Set the DTLS role.
        if description.type == "answer":
            for i, media in enumerate(description.media):
                if media.kind in ["audio", "video"]:
                    transceiver = self.__getTransceiverByMLineIndex(i)
                    transceiver.receiver.transport._set_role(media.dtls.role)
                elif media.kind == "application":
                    self.__sctp.transport._set_role(media.dtls.role)

        # Configure the directions, which are inactive for stopped transceivers.
        for t in self.__transceivers:
            if description.type in ["answer", "pranswer"]:
                if t.stopped:
                    t._setCurrentDirection("inactive")
                else:
                    t._setCurrentDirection(
                        and_direction(t.direction, t._offerDirection)
                    )

        # Store the description before the signaling state changes, so that
        # it is visible at once (W3C webrtc-pc, set the session description).
        if description.type == "answer":
            self.__currentLocalDescription = description
            self.__pendingLocalDescription = None
            self.__negotiatedBundleTag = get_bundle_tag(description)
        else:
            self.__pendingLocalDescription = description
        if description.type == "offer":
            self.__setSignalingState("have-local-offer")
        elif description.type == "answer":
            self.__setSignalingState("stable")

        # Gather candidates and add them to the stored description. A pending
        # __connect() task may run meanwhile, as the description is applied.
        await self.__gather()
        for i, media in enumerate(description.media):
            if is_rejected(media):
                continue
            elif media.kind in ["audio", "video"]:
                transceiver = self.__getTransceiverByMLineIndex(i)
                add_transport_description(media, transceiver.receiver.transport)
            elif media.kind == "application":
                add_transport_description(media, self.__sctp.transport)

        # Start the transports and transceivers in the background.
        asyncio.ensure_future(self.__connect())

    async def setRemoteDescription(
        self, sessionDescription: RTCSessionDescription
    ) -> None:
        """
        Changes the remote description associated with the connection.

        :param sessionDescription: An :class:`RTCSessionDescription` created from
                                    information received over the signaling channel.
        """
        self.__log_debug(
            "setRemoteDescription(%s)\n%s",
            sessionDescription.type,
            sessionDescription.sdp,
        )

        # parse and validate description
        description = sdp.SessionDescription.parse(sessionDescription.sdp)
        description.type = sessionDescription.type
        self.__validate_description(description, is_local=False)

        # apply description
        iceCandidates: dict[RTCIceTransport, sdp.MediaDescription] = {}
        rejectedTransceivers: list[RTCRtpTransceiver] = []
        trackEvents = []
        for i, media in enumerate(description.media):
            dtlsTransport: Optional[RTCDtlsTransport] = None
            self.__seenMids.add(media.rtp.muxId)
            if media.kind in ["audio", "video"]:
                # Find the transceiver, but do not give a rejected "m=" section
                # one which has no MID yet.
                rejected = is_rejected(media)
                transceiver = None
                for t in self.__transceivers:
                    if t.kind == media.kind and (
                        t.mid == media.rtp.muxId or (t.mid is None and not rejected)
                    ):
                        transceiver = t
                        break
                if transceiver is None:
                    transceiver = self.__createTransceiver(
                        direction="recvonly", kind=media.kind
                    )
                if transceiver.mid is None:
                    transceiver._set_mid(media.rtp.muxId)
                    transceiver._set_mline_index(i)

                # Negotiate the codecs, unless the "m=" section is rejected.
                if not rejected:
                    common = filter_preferred_codecs(
                        find_common_codecs(CODECS[media.kind], media.rtp.codecs),
                        transceiver._preferred_codecs,
                    )
                    if common:
                        transceiver._codecs = common
                        transceiver._headerExtensions = find_common_header_extensions(
                            HEADER_EXTENSIONS[media.kind], media.extmap
                        )
                    elif description.type == "offer":
                        # An offered "m=" section without a supported format is
                        # rejected in the answer (RFC 9429 section 5.3.1).
                        rejected = True
                    else:
                        raise OperationError(
                            f"Failed to set remote {media.kind} description "
                            "send parameters"
                        )

                # A rejected "m=" section is inactive and stops its transceiver
                # once the description is stored (W3C webrtc-pc, set the session
                # description).
                if rejected:
                    direction = "inactive"
                else:
                    direction = reverse_direction(media.direction)
                if description.type in ["answer", "pranswer"]:
                    transceiver._setCurrentDirection(direction)
                else:
                    transceiver._offerDirection = direction
                if rejected:
                    rejectedTransceivers.append(transceiver)
                    continue

                # Create the remote track if media is received.
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
                dtlsTransport = transceiver.receiver.transport
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
                if description.type == "offer" and media.dtls.role == "client":
                    dtlsTransport._set_role(role="server")
                if description.type == "answer":
                    dtlsTransport._set_role(
                        role="server" if media.dtls.role == "client" else "client"
                    )

        # Store the description before the first await, so that a pending
        # __connect() task does not see it half applied, and before the
        # signaling state changes (W3C webrtc-pc, set the session description).
        if description.type == "answer":
            self.__currentRemoteDescription = description
            self.__pendingRemoteDescription = None
            self.__negotiatedBundleTag = get_bundle_tag(description)
        else:
            self.__pendingRemoteDescription = description
        if description.type == "offer":
            self.__setSignalingState("have-remote-offer")
        elif description.type == "answer":
            self.__setSignalingState("stable")

        # Stop the transceivers of rejected "m=" sections.
        for transceiver in rejectedTransceivers:
            if not transceiver.stopped:
                await transceiver.stop()

        # Move the bundled media to the transport of the BUNDLE-tagged section.
        bundle = next((x for x in description.group if x.semantic == "BUNDLE"), None)
        if bundle and bundle.items:
            # find main media stream
            primaryMid = bundle.items[0]
            primaryTransport = None
            for transceiver in self.__transceivers:
                if transceiver.mid == primaryMid:
                    primaryTransport = transceiver.receiver.transport
                    break
            if self.__sctp and self.__sctp.mid == primaryMid:
                primaryTransport = self.__sctp.transport

            # The loop above leaves the last section on a shared transport, but the
            # tagged section's candidates apply to the group (RFC 9143 sections 7.4
            # and 10).
            primaryMedia = next(
                (m for m in description.media if m.rtp.muxId == primaryMid), None
            )
            if primaryTransport is not None and primaryMedia is not None:
                iceCandidates[primaryTransport.transport] = primaryMedia

            # replace transport for bundled media
            otherMids = bundle.items[1:]
            for transceiver in self.__transceivers:
                if (
                    transceiver.mid in otherMids
                    and transceiver.receiver.transport != primaryTransport
                ):
                    transceiver.receiver.setTransport(primaryTransport)
                    transceiver.sender.setTransport(primaryTransport)
                    transceiver._bundled = True
            if (
                self.__sctp
                and self.__sctp.mid in otherMids
                and self.__sctp.transport != primaryTransport
            ):
                self.__sctp.setTransport(primaryTransport)
                self.__sctp._bundled = True

        # Discard the transports replaced by the bundled one, and those left
        # only to stopped transceivers.
        for dtlsTransport in await self.__discardUnusedTransports():
            iceCandidates.pop(dtlsTransport.transport, None)

        # add remote candidates
        coros = [
            add_remote_candidates(iceTransport, media)
            for iceTransport, media in iceCandidates.items()
        ]
        await asyncio.gather(*coros)

        # FIXME: in aiortc 2.0.0 emit RTCTrackEvent directly
        for event in trackEvents:
            self.emit("track", event.track)

        # Start the transports and transceivers in the background.
        asyncio.ensure_future(self.__connect())

    async def __connect(self) -> None:
        # The connection may have been closed before this task got to run.
        if self.__isClosed:
            return

        for transceiver in self.__transceivers:
            dtlsTransport = transceiver.receiver.transport
            iceTransport = dtlsTransport.transport
            if can_start_ice(iceTransport) and transceiver in self.__remoteIce:
                await iceTransport.start(self.__remoteIce[transceiver])
                if can_start_dtls(dtlsTransport):
                    await dtlsTransport.start(self.__remoteDtls[transceiver])
                if dtlsTransport.state == "connected":
                    # The format to send is chosen when an answer is applied,
                    # not when a new offer is (RFC 9429 section 5.11).
                    if (
                        transceiver.currentDirection in ["sendonly", "sendrecv"]
                        and self.signalingState == "stable"
                    ):
                        await transceiver.sender.send(self.__localRtp(transceiver))
                    if transceiver.currentDirection in ["recvonly", "sendrecv"]:
                        await transceiver.receiver.receive(
                            self.__remoteRtp(transceiver)
                        )
        if self.__sctp:
            dtlsTransport = self.__sctp.transport
            iceTransport = dtlsTransport.transport
            if can_start_ice(iceTransport) and self.__sctp in self.__remoteIce:
                await iceTransport.start(self.__remoteIce[self.__sctp])
                if can_start_dtls(dtlsTransport):
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
        if len(self.__transceivers) > 0 or self.__sctp:
            if len(self.__transceivers) > 0:
                parameters = self.__transceivers[
                    0
                ].receiver.transport.transport.iceGatherer.getLocalParameters()
            else:
                parameters = (
                    self.__sctp.transport.transport.iceGatherer.getLocalParameters()
                )
            iceGatherer = RTCIceGatherer(
                iceServers=self.__configuration.iceServers,
                iceTransportPolicy=self.__configuration.iceTransportPolicy,
                local_username=parameters.usernameFragment,
                local_password=parameters.password,
            )
        else:
            iceGatherer = RTCIceGatherer(
                iceServers=self.__configuration.iceServers,
                iceTransportPolicy=self.__configuration.iceTransportPolicy,
            )

        iceGatherer.on("statechange", self.__updateIceGatheringState)
        iceTransport = RTCIceTransport(iceGatherer)
        iceTransport.on("statechange", self.__updateIceConnectionState)
        iceTransport.on("statechange", self.__updateConnectionState)
        self.__iceTransports.add(iceTransport)

        # create DTLS transport
        dtlsTransport = RTCDtlsTransport(iceTransport, self.__certificates)
        dtlsTransport.on("statechange", self.__updateConnectionState)
        self.__dtlsTransports.add(dtlsTransport)

        # update states
        self.__updateIceGatheringState()
        self.__updateIceConnectionState()
        self.__updateConnectionState()

        return dtlsTransport

    def __createSctpTransport(self) -> None:
        dtlsTransport = None
        bundled = (
            self.__configuration.bundlePolicy == RTCBundlePolicy.MAX_BUNDLE
            and len(self.__transceivers) > 0
        )
        if bundled:
            dtlsTransport = self.__transceivers[0].receiver.transport
        else:
            dtlsTransport = self.__createDtlsTransport()
        self.__sctp = RTCSctpTransport(dtlsTransport)
        self.__sctp._bundled = bundled
        self.__sctp.mid = None

        @self.__sctp.on("datachannel")
        def on_datachannel(channel: RTCDataChannel) -> None:
            self.emit("datachannel", channel)

    def __createTransceiver(
        self, direction: str, kind: str, sender_track: Optional[MediaStreamTrack] = None
    ) -> RTCRtpTransceiver:
        dtlsTransport = None
        bundled = False
        if self.__configuration.bundlePolicy == RTCBundlePolicy.MAX_BUNDLE:
            if len(self.__transceivers) > 0:
                dtlsTransport = self.__transceivers[0].receiver.transport
                bundled = True
            elif self.__sctp:
                dtlsTransport = self.__sctp.transport
                bundled = True
        elif self.__configuration.bundlePolicy == RTCBundlePolicy.BALANCED:
            # The transport of a stopped transceiver may have been discarded.
            transceiver = next(
                filter(lambda t: t.kind == kind and not t.stopped, self.__transceivers),
                None,
            )
            if transceiver:
                dtlsTransport = transceiver.receiver.transport
                bundled = True

        if not dtlsTransport:
            dtlsTransport = self.__createDtlsTransport()

        transceiver = RTCRtpTransceiver(
            direction=direction,
            kind=kind,
            sender=RTCRtpSender(sender_track or kind, dtlsTransport),
            receiver=RTCRtpReceiver(kind, dtlsTransport),
        )
        transceiver.receiver._set_rtcp_cname(self.__cname)
        transceiver.receiver._set_rtcp_ssrc(transceiver.sender._ssrc)
        transceiver.sender._stream_id = self.__stream_id
        transceiver._bundled = bundled
        self.__transceivers.append(transceiver)
        return transceiver

    async def __discardUnusedTransports(self) -> set[RTCDtlsTransport]:
        """
        Stop and return the transports which no transceiver or data channel
        uses any more, such as those of rejected "m=" sections which are not
        bundled (RFC 9429 section 5.11).
        """
        used = {t.receiver.transport for t in self.__transceivers if not t.stopped}
        if self.__sctp is not None:
            used.add(self.__sctp.transport)

        # Closing every transport would make the remote end close the
        # connection, so they are kept if none is used.
        if not used:
            return set()

        unused = self.__dtlsTransports - used
        for dtlsTransport in unused:
            await dtlsTransport.stop()
            await dtlsTransport.transport.stop()
            self.__dtlsTransports.discard(dtlsTransport)
            self.__iceTransports.discard(dtlsTransport.transport)
        self.__updateIceGatheringState()
        self.__updateIceConnectionState()
        self.__updateConnectionState()
        return unused

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

    def __log_debug(self, msg: str, *args: object) -> None:
        logger.debug(f"RTCPeerConnection() {msg}", *args)

    def __remoteDescription(self) -> Optional[sdp.SessionDescription]:
        return self.__pendingRemoteDescription or self.__currentRemoteDescription

    def __remoteRtp(self, transceiver: RTCRtpTransceiver) -> RTCRtpReceiveParameters:
        mline_index = transceiver._get_mline_index()
        media = self.__remoteDescription().media[mline_index]

        # Incoming RTP uses the payload types from the local description,
        # which may differ from the remote one (RFC 9429 section 5.9).
        codecs = find_common_codecs(
            transceiver._codecs,
            self.__localDescription().media[mline_index].rtp.codecs,
        )

        receiveParameters = RTCRtpReceiveParameters(
            codecs=codecs,
            headerExtensions=transceiver._headerExtensions,
            muxId=media.rtp.muxId,
            rtcp=media.rtp.rtcp,
        )
        if len(media.ssrc):
            # Create encodings for codecs other than RTX first, as the codec an
            # RTX codec repairs may be listed after it (RFC 4588 section 8.1).
            encodings: dict[int, RTCRtpDecodingParameters] = {}
            for codec in codecs:
                if not is_rtx(codec):
                    encodings[codec.payloadType] = RTCRtpDecodingParameters(
                        ssrc=media.ssrc[0].ssrc, payloadType=codec.payloadType
                    )

            # Then map each RTX codec to the encoding of the codec it repairs.
            for codec in filter(is_rtx, codecs):
                apt = codec.parameters.get("apt")
                if isinstance(apt, int) and apt in encodings and len(media.ssrc) == 2:
                    encodings[apt].rtx = RTCRtpRtxParameters(ssrc=media.ssrc[1].ssrc)
            receiveParameters.encodings = list(encodings.values())
        return receiveParameters

    def __setSignalingState(self, state: str) -> None:
        self.__signalingState = state
        self.emit("signalingstatechange")

    def __updateConnectionState(self) -> None:
        # compute new state
        # NOTE: we do not have a "disconnected" state
        dtlsStates = set(map(lambda x: x.state, self.__dtlsTransports))
        iceStates = set(map(lambda x: x.state, self.__iceTransports))
        if self.__isClosed:
            state = "closed"
        elif "failed" in iceStates or "failed" in dtlsStates:
            state = "failed"
        elif not iceStates.difference(["new", "closed"]) and not dtlsStates.difference(
            ["new", "closed"]
        ):
            state = "new"
        elif "checking" in iceStates or "connecting" in dtlsStates:
            state = "connecting"
        elif "new" in dtlsStates:
            # this avoids a spurious connecting -> connected -> connecting
            # transition after ICE connects but before DTLS starts
            state = "connecting"
        else:
            state = "connected"

        # update state
        if state != self.__connectionState:
            self.__log_debug("connectionState %s -> %s", self.__connectionState, state)
            self.__connectionState = state
            self.emit("connectionstatechange")

        # if all DTLS connections are closed, initiate a shutdown
        if (
            not self.__isClosed
            and self.__closeTask is None
            and dtlsStates == set(["closed"])
        ):
            self.__closeTask = asyncio.ensure_future(self.close())

    def __updateIceConnectionState(self) -> None:
        # compute new state
        # NOTE: we do not have "connected" or "disconnected" states
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
            self.__log_debug(
                "iceConnectionState %s -> %s", self.__iceConnectionState, state
            )
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
            self.__log_debug(
                "iceGatheringState %s -> %s", self.__iceGatheringState, state
            )
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
                        "Cannot handle offer in signaling state "
                        f'"{self.signalingState}"'
                    )
            elif description.type == "answer":
                if self.signalingState not in [
                    "have-remote-offer",
                    "have-local-pranswer",
                ]:
                    raise InvalidStateError(
                        "Cannot handle answer in signaling state "
                        f'"{self.signalingState}"'
                    )
        else:
            if description.type == "offer":
                if self.signalingState not in ["stable", "have-remote-offer"]:
                    raise InvalidStateError(
                        "Cannot handle offer in signaling state "
                        f'"{self.signalingState}"'
                    )
            elif description.type == "answer":
                if self.signalingState not in [
                    "have-local-offer",
                    "have-remote-pranswer",
                ]:
                    raise InvalidStateError(
                        "Cannot handle answer in signaling state "
                        f'"{self.signalingState}"'
                    )

            # Rejecting the BUNDLE-tagged "m=" section while a BUNDLE group remains
            # moves browsers to a new transport aiortc cannot follow.
            if get_bundle_mids(description) and any(
                is_rejected(media) and media.rtp.muxId == self.__negotiatedBundleTag
                for media in description.media
            ):
                raise OperationError(
                    "Rejecting the BUNDLE-tagged media section is not supported"
                )

        for media in description.media:
            # A rejected "m=" section may omit all its attributes (RFC 3264
            # section 8.2).
            if media.kind in ["audio", "video"] and is_rejected(media):
                continue

            # check ICE credentials were provided
            if not media.ice.usernameFragment or not media.ice.password:
                raise ValueError("ICE username fragment or password is missing")

            # check DTLS role is allowed
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
