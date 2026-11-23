import asyncio
import logging
import random
import time
import traceback
import uuid
from collections.abc import Callable
from typing import Optional, Union

from av import AudioFrame
from av.frame import Frame

from . import clock, rtp
from .codecs import get_capabilities, get_encoder, is_rtx
from .codecs.base import Encoder
from .exceptions import InvalidStateError
from .mediastreams import MediaStreamError, MediaStreamTrack
from .rtcdtlstransport import RTCDtlsTransport
from .rtcrtpparameters import (
    RTCRtpCapabilities,
    RTCRtpCodecParameters,
    RTCRtpSendParameters,
)
from .rtp import (
    RTCP_PSFB_APP,
    RTCP_PSFB_FIR,
    RTCP_PSFB_PLI,
    RTCP_RTPFB_NACK,
    RTP_HISTORY_SIZE,
    AnyRtcpPacket,
    RtcpByePacket,
    RtcpPsfbPacket,
    RtcpRrPacket,
    RtcpRtpfbPacket,
    RtcpSdesPacket,
    RtcpSenderInfo,
    RtcpSrPacket,
    RtpPacket,
    unpack_remb_fci,
    wrap_rtx,
)
from .stats import (
    RTCOutboundRtpStreamStats,
    RTCRemoteInboundRtpStreamStats,
    RTCStatsReport,
)
from .utils import random16, random32, uint16_add, uint32_add

logger = logging.getLogger(__name__)

RTT_ALPHA = 0.85


def random_sequence_number() -> int:
    """
    Generate a random RTP sequence number.

    The sequence number is chosen in the lower half of the allowed range in
    order to avoid wraparounds which break SRTP decryption.

    See:
    https://chromiumdash.appspot.com/commit/13b327b05fa3788b4daa9c3463e13282824cb320
    """
    return random16() % 32768


class RTCEncodedFrame:
    def __init__(self, payloads: list[bytes], timestamp: int, audio_level: int):
        self.payloads = payloads
        self.timestamp = timestamp
        self.audio_level = audio_level


class RtpTimeline:
    """
    Map encoder timestamps to RTP timestamps, which must increase monotonically
    and linearly in time (RFC 3550 section 5.1), even when the source changes.
    """

    def __init__(self, clock_rate: int) -> None:
        self.__clock_rate = clock_rate
        self.__frame_duration = 0
        self.__last_time = 0.0
        self.__last_timestamp: Optional[int] = None
        self.__origin = random32()
        self.__restart = False

    def restart(self) -> None:
        """
        Start a new source, whose timestamps are unrelated to the previous ones.
        """
        self.__restart = True

    def timestamp(self, encoder_timestamp: int) -> int:
        """
        Return the RTP timestamp of a frame.
        """
        now = time.monotonic()
        if self.__restart and self.__last_timestamp is not None:
            # Carry on from the previous frame, after the elapsed time but
            # without overlapping it.
            elapsed = int((now - self.__last_time) * self.__clock_rate)
            target = uint32_add(
                self.__last_timestamp, max(elapsed, self.__frame_duration)
            )
            self.__origin = uint32_add(target, -encoder_timestamp)

        timestamp = uint32_add(self.__origin, encoder_timestamp)
        if self.__last_timestamp is not None and not self.__restart:
            self.__frame_duration = uint32_add(timestamp, -self.__last_timestamp)
        self.__last_time = now
        self.__last_timestamp = timestamp
        self.__restart = False
        return timestamp


class RTCRtpSender:
    """
    The :class:`RTCRtpSender` interface provides the ability to control and
    obtain details about how a particular :class:`MediaStreamTrack` is encoded
    and sent to a remote peer.

    :param trackOrKind: Either a :class:`MediaStreamTrack` instance or a
                         media kind (`'audio'` or `'video'`).
    :param transport: An :class:`RTCDtlsTransport`.
    """

    def __init__(
        self, trackOrKind: Union[MediaStreamTrack, str], transport: RTCDtlsTransport
    ) -> None:
        if transport.state == "closed":
            raise InvalidStateError

        self.__track_replaced = asyncio.Event()
        if isinstance(trackOrKind, MediaStreamTrack):
            self.__kind = trackOrKind.kind
            self.replaceTrack(trackOrKind)
        else:
            self.__kind = trackOrKind
            self.replaceTrack(None)
        self.__cname: Optional[str] = None
        self.__codec: Optional[RTCRtpCodecParameters] = None
        self._ssrc = random32()
        self._rtx_ssrc = random32()
        # FIXME: how should this be initialised?
        self._stream_id = str(uuid.uuid4())
        self._enabled = True
        self.__encoder: Optional[Encoder] = None
        self.__force_keyframe = False
        self.__loop = asyncio.get_event_loop()
        self.__mid: Optional[str] = None
        self.__rtp_exited = asyncio.Event()
        self.__rtp_header_extensions_map = rtp.HeaderExtensionsMap()
        self.__rtp_started = asyncio.Event()
        self.__rtp_task: Optional[asyncio.Future[None]] = None
        self.__rtp_history: dict[int, RtpPacket] = {}
        self.__rtcp_exited = asyncio.Event()
        self.__rtcp_started = asyncio.Event()
        self.__rtcp_task: Optional[asyncio.Future[None]] = None
        self.__rtx_payload_type: Optional[int] = None
        self.__rtx_sequence_number = random_sequence_number()
        self.__started = False
        self.__stats = RTCStatsReport()
        # SSRCs are only unique within an RTP session (RFC 3550 section 3), so
        # stats IDs are scoped to this sender.
        self.__stats_id = str(id(self))
        self.__stopped = False
        self.__transport = transport

        # RTCP sender reports and round-trip time.
        self.__lsr: Optional[int] = None
        self.__lsr_time: Optional[float] = None
        self.__ntp_timestamp = 0
        self.__rtp_timestamp = 0
        self.__octet_count = 0
        self.__packet_count = 0
        self.__rtt: Optional[float] = None

        # Frames, NACKs and retransmissions are counted for stats, not RTCP reports.
        self.__frames_encoded = 0
        self.__frames_sent = 0
        self.__nack_count = 0
        self.__retransmitted_bytes_sent = 0
        self.__retransmitted_packets_sent = 0

        # logging
        self.__log_debug: Callable[..., None] = lambda *args: None
        if logger.isEnabledFor(logging.DEBUG):
            self.__log_debug = lambda msg, *args: logger.debug(
                f"RTCRtpSender(%s) {msg}", self.__kind, *args
            )

    @property
    def kind(self) -> str:
        return self.__kind

    @property
    def track(self) -> Optional[MediaStreamTrack]:
        """
        The :class:`MediaStreamTrack` which is being handled by the sender, or
        `None`.
        """
        return self.__track

    @property
    def transport(self) -> RTCDtlsTransport:
        """
        The :class:`RTCDtlsTransport` over which media data for the track is
        transmitted.
        """
        return self.__transport

    @classmethod
    def getCapabilities(self, kind: str) -> RTCRtpCapabilities:
        """
        Returns the most optimistic view of the system's capabilities for
        sending media of the given `kind`.

        :rtype: :class:`RTCRtpCapabilities`
        """
        return get_capabilities(kind)

    async def getStats(self) -> RTCStatsReport:
        """
        Returns statistics about the RTP sender.

        :rtype: :class:`RTCStatsReport`
        """
        # Link the local and remote statistics, once an RTCP RR was received.
        local_id = self.__stats_object_id("outbound-rtp", self._ssrc)
        remote = self.__stats.get(
            self.__stats_object_id("remote-inbound-rtp", self._ssrc)
        )
        if remote is not None:
            remote.localId = local_id

        # Frame counters MUST NOT exist for audio (webrtc-stats, framesEncoded).
        is_video = self.__kind == "video"

        self.__stats.add(
            RTCOutboundRtpStreamStats(
                # RTCStats
                timestamp=clock.current_datetime(),
                type="outbound-rtp",
                id=local_id,
                # RTCStreamStats
                ssrc=self._ssrc,
                kind=self.__kind,
                transportId=self.transport._stats_id,
                # RTCSentRtpStreamStats
                packetsSent=self.__packet_count + self.__retransmitted_packets_sent,
                bytesSent=self.__octet_count + self.__retransmitted_bytes_sent,
                # RTCOutboundRtpStreamStats
                trackId=str(id(self.track)),
                nackCount=self.__nack_count,
                framesEncoded=self.__frames_encoded if is_video else None,
                framesSent=self.__frames_sent if is_video else None,
                remoteId=remote.id if remote is not None else None,
            )
        )
        self.__stats.update(self.transport._get_stats())

        return self.__stats

    def replaceTrack(self, track: Optional[MediaStreamTrack]) -> None:
        """
        Replace the track being sent, without renegotiation.

        The new track is sent on the same RTP stream, whose timestamps carry
        on. With `None`, or once a track has ended, nothing is sent until the
        track is replaced.

        :param track: A :class:`MediaStreamTrack` of the sender's kind, or `None`.
        :raises TypeError: If the track's kind differs from the sender's.
        """
        if track is not None and track.kind != self.__kind:
            raise TypeError(f"Expected a track of kind '{self.__kind}'")

        self.__track = track
        self.__track_replaced.set()
        if track is not None:
            self._track_id = track.id
        else:
            self._track_id = str(uuid.uuid4())

    def setTransport(self, transport: RTCDtlsTransport) -> None:
        self.__transport = transport

    async def send(self, parameters: RTCRtpSendParameters) -> None:
        """
        Attempt to set the parameters controlling the sending of media.

        :param parameters: The :class:`RTCRtpSendParameters` for the sender.
        """
        if self.__stopped:
            return

        self.__cname = parameters.rtcp.cname
        self.__mid = parameters.muxId

        # Send with the first codec that is not RTX, which only repairs
        # the codec being sent (RFC 9429 section 5.11).
        codec = next(c for c in parameters.codecs if not is_rtx(c))
        if codec != self.__codec:
            # Keep the SSRC even if the clock rate changes, unlike RFC 7160 section
            # 4.1, as receivers routing by signalled SSRCs would drop a new one.
            self.__codec = codec

        # Make note of the RTX payload type that repairs this codec.
        self.__rtx_payload_type = next(
            (
                c.payloadType
                for c in parameters.codecs
                if is_rtx(c) and c.parameters["apt"] == codec.payloadType
            ),
            None,
        )

        # Receive RTCP through the transport, and use the RTP header extension
        # IDs of the latest negotiation.
        self.__transport._register_rtp_sender(self, parameters)
        self.__rtp_header_extensions_map.configure(parameters)

        if not self.__started:
            self.__rtp_task = asyncio.ensure_future(self._run_rtp())
            self.__rtcp_task = asyncio.ensure_future(self._run_rtcp())
            self.__started = True

    async def stop(self) -> None:
        """
        Irreversibly stop the sender.
        """
        self.__stopped = True
        if self.__started:
            self.__transport._unregister_rtp_sender(self)

            # Shut down the RTP and RTCP tasks.
            await asyncio.gather(self.__rtp_started.wait(), self.__rtcp_started.wait())
            self.__rtp_task.cancel()
            self.__rtcp_task.cancel()
            await asyncio.gather(self.__rtp_exited.wait(), self.__rtcp_exited.wait())

    async def _handle_rtcp_packet(self, packet: AnyRtcpPacket) -> None:
        if isinstance(packet, (RtcpRrPacket, RtcpSrPacket)):
            for report in filter(lambda x: x.ssrc == self._ssrc, packet.reports):
                # estimate round-trip time
                if self.__lsr == report.lsr and report.dlsr:
                    rtt = time.monotonic() - self.__lsr_time - (report.dlsr / 65536)
                    if self.__rtt is None:
                        self.__rtt = rtt
                    else:
                        self.__rtt = RTT_ALPHA * self.__rtt + (1 - RTT_ALPHA) * rtt

                self.__stats.add(
                    RTCRemoteInboundRtpStreamStats(
                        # RTCStats
                        timestamp=clock.current_datetime(),
                        type="remote-inbound-rtp",
                        id=self.__stats_object_id("remote-inbound-rtp", report.ssrc),
                        # RTCStreamStats
                        ssrc=report.ssrc,
                        kind=self.__kind,
                        transportId=self.transport._stats_id,
                        # RTCReceivedRtpStreamStats
                        packetsReceived=self.__packet_count - report.packets_lost,
                        packetsLost=report.packets_lost,
                        jitter=report.jitter / self.__codec.clockRate,
                        # RTCRemoteInboundRtpStreamStats
                        roundTripTime=self.__rtt,
                        fractionLost=report.fraction_lost / 256,
                    )
                )
        elif isinstance(packet, RtcpRtpfbPacket) and packet.fmt == RTCP_RTPFB_NACK:
            self.__nack_count += 1
            for seq in packet.lost:
                await self._retransmit(seq)
        elif isinstance(packet, RtcpPsfbPacket) and packet.fmt in (
            RTCP_PSFB_FIR,  # Full Instantaneous Resolution
            RTCP_PSFB_PLI,  # Picture Loss Indication
        ):
            self._send_keyframe()
        elif isinstance(packet, RtcpPsfbPacket) and packet.fmt == RTCP_PSFB_APP:
            try:
                bitrate, ssrcs = unpack_remb_fci(packet.fci)
                if self._ssrc in ssrcs:
                    self.__log_debug(
                        "- receiver estimated maximum bitrate %d bps", bitrate
                    )
                    if self.__encoder and hasattr(self.__encoder, "target_bitrate"):
                        self.__encoder.target_bitrate = bitrate
            except ValueError:
                pass

    async def _next_encoded_frame(
        self, codec: RTCRtpCodecParameters, track: MediaStreamTrack
    ) -> Optional[RTCEncodedFrame]:
        # Get [Frame|Packet].
        data = await track.recv()

        # If the sender is disabled, drop the frame instead of encoding it.
        # We still want to read from the track in order to avoid frames
        # accumulating in memory.
        if not self._enabled:
            return None

        audio_level = None

        if self.__encoder is None:
            self.__encoder = get_encoder(codec)

        if isinstance(data, Frame):
            # Encode the frame.
            if isinstance(data, AudioFrame):
                audio_level = rtp.compute_audio_level_dbov(data)

            force_keyframe = self.__force_keyframe
            self.__force_keyframe = False
            payloads, timestamp = await self.__loop.run_in_executor(
                None, self.__encoder.encode, data, force_keyframe
            )
        else:
            # Pack the pre-encoded data.
            payloads, timestamp = self.__encoder.pack(data)

        # If the encoder did not return any payloads, return `None`.
        # This may be due to a delay caused by resampling.
        if not payloads:
            return None

        return RTCEncodedFrame(payloads, timestamp, audio_level)

    async def _retransmit(self, sequence_number: int) -> None:
        """
        Retransmit an RTP packet which was reported as lost.
        """
        packet = self.__rtp_history.get(sequence_number % RTP_HISTORY_SIZE)
        if packet and packet.sequence_number == sequence_number:
            payload_size = len(packet.payload)
            if self.__rtx_payload_type is not None:
                packet = wrap_rtx(
                    packet,
                    payload_type=self.__rtx_payload_type,
                    sequence_number=self.__rtx_sequence_number,
                    ssrc=self._rtx_ssrc,
                )
                self.__rtx_sequence_number = uint16_add(self.__rtx_sequence_number, 1)

            self.__log_debug("> %s", packet)
            packet_bytes = packet.serialize(self.__rtp_header_extensions_map)
            await self.transport._send_rtp(packet_bytes)

            # Retransmissions count towards the stream's stats (webrtc-stats,
            # "outbound-rtp"), and RTX ones are not part of its RTCP sender reports.
            self.__retransmitted_bytes_sent += payload_size
            self.__retransmitted_packets_sent += 1

    def _send_keyframe(self) -> None:
        """
        Request the next frame to be a keyframe.
        """
        self.__force_keyframe = True

    async def _run_rtp(self) -> None:
        self.__log_debug("- RTP started")
        self.__rtp_started.set()

        codec = self.__codec
        sequence_number = random_sequence_number()
        timeline = RtpTimeline(codec.clockRate)
        sending_resumed = True
        source = None
        track_ended = False
        try:
            while True:
                # A new codec needs a new encoder, and a new clock rate a new
                # timeline.
                if self.__codec is not codec:
                    if self.__codec.clockRate != codec.clockRate:
                        timeline = RtpTimeline(self.__codec.clockRate)
                    codec = self.__codec
                    self.__encoder = None
                    source = None

                track = self.__track
                if not track or track_ended:
                    sending_resumed = True
                    source = None

                    # Nothing to send until the track is replaced. There is no
                    # await since reading the track, so no replacement is missed.
                    self.__track_replaced.clear()
                    await self.__track_replaced.wait()
                    track_ended = False
                    continue

                # Frames from a new track may differ in format, and their
                # timestamps are unrelated to those of the previous track.
                if track is not source:
                    source = track
                    if self.__encoder is not None:
                        self.__encoder.reset()
                    timeline.restart()

                # Fetch the next encoded frame. This can be `None` if the sender
                # is disabled, in which case we just continue the loop.
                try:
                    enc_frame = await self._next_encoded_frame(codec, track)
                except MediaStreamError:
                    # An ended track remains the sender's track, but is not sent
                    # until it is replaced (W3C webrtc-pc, RTCRtpSender.track).
                    self.__log_debug("- track ended")
                    track_ended = True
                    continue
                if enc_frame is None:
                    if not self._enabled:
                        sending_resumed = True
                    continue
                self.__frames_encoded += 1

                # A frame encoded while the codec changed is dropped.
                if self.__codec is not codec:
                    continue

                timestamp = timeline.timestamp(enc_frame.timestamp)

                for i, payload in enumerate(enc_frame.payloads):
                    packet = RtpPacket(
                        payload_type=codec.payloadType,
                        sequence_number=sequence_number,
                        timestamp=timestamp,
                    )
                    packet.ssrc = self._ssrc
                    packet.payload = payload
                    if self.__kind == "audio":
                        # For audio, the first packet after a gap in transmission
                        # starts a talkspurt and is marked (RFC 3551 section 4.1).
                        packet.marker = int(sending_resumed and i == 0)
                    else:
                        # For video, the marker bit flags the last packet of a
                        # frame (RFC 7741 section 4.1).
                        packet.marker = int(i == len(enc_frame.payloads) - 1)

                    # set header extensions
                    packet.extensions.abs_send_time = (
                        clock.current_ntp_time() >> 14
                    ) & 0x00FFFFFF
                    packet.extensions.mid = self.__mid
                    if enc_frame.audio_level is not None:
                        packet.extensions.audio_level = (False, -enc_frame.audio_level)

                    # send packet
                    self.__log_debug("> %s", packet)
                    self.__rtp_history[packet.sequence_number % RTP_HISTORY_SIZE] = (
                        packet
                    )
                    packet_bytes = packet.serialize(self.__rtp_header_extensions_map)
                    await self.transport._send_rtp(packet_bytes)

                    self.__ntp_timestamp = clock.current_ntp_time()
                    self.__rtp_timestamp = packet.timestamp
                    self.__octet_count += len(payload)
                    self.__packet_count += 1
                    sequence_number = uint16_add(sequence_number, 1)

                self.__frames_sent += 1
                sending_resumed = False
        except (asyncio.CancelledError, ConnectionError):
            pass
        except Exception:
            # we *need* to set __rtp_exited, otherwise RTCRtpSender.stop() will hang,
            # so issue a warning if we hit an unexpected exception
            self.__log_warning(traceback.format_exc())

        # stop track
        if self.__track:
            self.__track.stop()
            self.__track = None

        # release encoder
        self.__encoder = None

        self.__log_debug("- RTP finished")
        self.__rtp_exited.set()

    async def _run_rtcp(self) -> None:
        self.__log_debug("- RTCP started")
        self.__rtcp_started.set()

        try:
            while True:
                # The interval between RTCP packets is varied randomly over the
                # range [0.5, 1.5] times the calculated interval.
                await asyncio.sleep(0.5 + random.random())

                # RTCP SR
                packets: list[AnyRtcpPacket] = [
                    RtcpSrPacket(
                        ssrc=self._ssrc,
                        sender_info=RtcpSenderInfo(
                            ntp_timestamp=self.__ntp_timestamp,
                            rtp_timestamp=self.__rtp_timestamp,
                            packet_count=self.__packet_count & 0xFFFFFFFF,
                            octet_count=self.__octet_count & 0xFFFFFFFF,
                        ),
                    )
                ]
                self.__lsr = ((self.__ntp_timestamp) >> 16) & 0xFFFFFFFF
                self.__lsr_time = time.monotonic()

                # RTCP SDES
                if self.__cname is not None:
                    packets.append(RtcpSdesPacket.cname(self._ssrc, self.__cname))

                await self._send_rtcp(packets)
        except asyncio.CancelledError:
            pass

        # RTCP BYE
        packet = RtcpByePacket(sources=[self._ssrc])
        await self._send_rtcp([packet])

        self.__log_debug("- RTCP finished")
        self.__rtcp_exited.set()

    async def _send_rtcp(self, packets: list[AnyRtcpPacket]) -> None:
        payload = b""
        for packet in packets:
            self.__log_debug("> %s", packet)
            payload += bytes(packet)

        try:
            await self.transport._send_rtp(payload)
        except ConnectionError:
            pass

    def __log_warning(self, msg: str, *args: object) -> None:
        logger.warning(f"RTCRtpsender(%s) {msg}", self.__kind, *args)

    def __stats_object_id(self, stats_type: str, ssrc: int) -> str:
        return f"{stats_type}_{self.__stats_id}_{ssrc}"
