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
    RtcpSourceInfo,
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

        if isinstance(trackOrKind, MediaStreamTrack):
            self.__kind = trackOrKind.kind
            self.replaceTrack(trackOrKind)
        else:
            self.__kind = trackOrKind
            self.replaceTrack(None)
        self.__cname: Optional[str] = None
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
        self.__transport = transport

        # stats
        self.__lsr: Optional[int] = None
        self.__lsr_time: Optional[float] = None
        self.__ntp_timestamp = 0
        self.__rtp_timestamp = 0
        self.__octet_count = 0
        self.__packet_count = 0
        self.__rtt: Optional[float] = None

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
    def track(self) -> MediaStreamTrack:
        """
        The :class:`MediaStreamTrack` which is being handled by the sender.
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
        self.__stats.add(
            RTCOutboundRtpStreamStats(
                # RTCStats
                timestamp=clock.current_datetime(),
                type="outbound-rtp",
                id="outbound-rtp_" + str(id(self)),
                # RTCStreamStats
                ssrc=self._ssrc,
                kind=self.__kind,
                transportId=self.transport._stats_id,
                # RTCSentRtpStreamStats
                packetsSent=self.__packet_count,
                bytesSent=self.__octet_count,
                # RTCOutboundRtpStreamStats
                trackId=str(id(self.track)),
            )
        )
        self.__stats.update(self.transport._get_stats())

        return self.__stats

    def replaceTrack(self, track: Optional[MediaStreamTrack]) -> None:
        self.__track = track
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
        if not self.__started:
            self.__cname = parameters.rtcp.cname
            self.__mid = parameters.muxId

            # make note of the RTP header extension IDs
            self.__transport._register_rtp_sender(self, parameters)
            self.__rtp_header_extensions_map.configure(parameters)

            # make note of RTX payload type
            for codec in parameters.codecs:
                if (
                    is_rtx(codec)
                    and codec.parameters["apt"] == parameters.codecs[0].payloadType
                ):
                    self.__rtx_payload_type = codec.payloadType
                    break

            self.__rtp_task = asyncio.ensure_future(self._run_rtp(parameters.codecs[0]))
            self.__rtcp_task = asyncio.ensure_future(self._run_rtcp())
            self.__started = True

    async def stop(self) -> None:
        """
        Irreversibly stop the sender.
        """
        if self.__started:
            self.__transport._unregister_rtp_sender(self)

            # shutdown RTP and RTCP tasks
            await asyncio.gather(self.__rtp_started.wait(), self.__rtcp_started.wait())
            self.__rtp_task.cancel()
            self.__rtcp_task.cancel()
            await asyncio.gather(self.__rtp_exited.wait(), self.__rtcp_exited.wait())

    async def _handle_rtcp_packet(self, packet: AnyRtcpPacket) -> None:
        if isinstance(packet, (RtcpRrPacket, RtcpSrPacket)):
            for report in filter(lambda x: x.ssrc == self._ssrc, packet.reports):
                # estimate round-trip time
                if self.__lsr == report.lsr and report.dlsr:
                    rtt = time.time() - self.__lsr_time - (report.dlsr / 65536)
                    if self.__rtt is None:
                        self.__rtt = rtt
                    else:
                        self.__rtt = RTT_ALPHA * self.__rtt + (1 - RTT_ALPHA) * rtt

                self.__stats.add(
                    RTCRemoteInboundRtpStreamStats(
                        # RTCStats
                        timestamp=clock.current_datetime(),
                        type="remote-inbound-rtp",
                        id="remote-inbound-rtp_" + str(id(self)),
                        # RTCStreamStats
                        ssrc=packet.ssrc,
                        kind=self.__kind,
                        transportId=self.transport._stats_id,
                        # RTCReceivedRtpStreamStats
                        packetsReceived=self.__packet_count - report.packets_lost,
                        packetsLost=report.packets_lost,
                        jitter=report.jitter,
                        # RTCRemoteInboundRtpStreamStats
                        roundTripTime=self.__rtt,
                        fractionLost=report.fraction_lost,
                    )
                )
        elif isinstance(packet, RtcpRtpfbPacket) and packet.fmt == RTCP_RTPFB_NACK:
            for seq in packet.lost:
                await self._retransmit(seq)
        elif isinstance(packet, RtcpPsfbPacket) and packet.fmt == RTCP_PSFB_PLI:
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
        self, codec: RTCRtpCodecParameters
    ) -> Optional[RTCEncodedFrame]:
        # Get [Frame|Packet].
        data = await self.__track.recv()

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

    def _send_keyframe(self) -> None:
        """
        Request the next frame to be a keyframe.
        """
        self.__force_keyframe = True

    async def _run_rtp(self, codec: RTCRtpCodecParameters) -> None:
        self.__log_debug("- RTP started")
        self.__rtp_started.set()

        sequence_number = random_sequence_number()
        timestamp_origin = random32()
        try:
            while True:
                if not self.__track:
                    await asyncio.sleep(0.02)
                    continue

                # Fetch the next encoded frame. This can be `None` if the sender
                # is disabled, in which case we just continue the loop.
                enc_frame = await self._next_encoded_frame(codec)
                if enc_frame is None:
                    continue

                timestamp = uint32_add(timestamp_origin, enc_frame.timestamp)

                for i, payload in enumerate(enc_frame.payloads):
                    packet = RtpPacket(
                        payload_type=codec.payloadType,
                        sequence_number=sequence_number,
                        timestamp=timestamp,
                    )
                    packet.ssrc = self._ssrc
                    packet.payload = payload
                    packet.marker = (i == len(enc_frame.payloads) - 1) and 1 or 0

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
        except (asyncio.CancelledError, ConnectionError, MediaStreamError):
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
                self.__lsr_time = time.time()

                # RTCP SDES
                if self.__cname is not None:
                    packets.append(
                        RtcpSdesPacket(
                            chunks=[
                                RtcpSourceInfo(
                                    ssrc=self._ssrc,
                                    items=[(1, self.__cname.encode("utf8"))],
                                )
                            ]
                        )
                    )

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
