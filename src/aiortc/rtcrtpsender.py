import asyncio
import logging
import random
import time
import traceback
import uuid
from typing import Dict, List, Optional, Union
import datetime

from . import clock, rtp
from .codecs import get_capabilities, get_encoder, is_rtx
from .codecs.base import Encoder
from .exceptions import InvalidStateError
from .mediastreams import MediaStreamError, MediaStreamTrack
from .rtcrtpparameters import RTCRtpCodecParameters, RTCRtpSendParameters
from .rtp import (
    RTCP_PSFB_APP,
    RTCP_PSFB_PLI,
    RTCP_RTPFB_NACK,
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

from first_order_model.fom_wrapper import FirstOrderModel
import numpy as np
import concurrent
import cv2
import av
from PIL import Image
# instantiate and warm up the model
#time_before_instantiation = time.perf_counter()
#config_path = '/data/pantea/aiortc/nets_implementation/first_order_model/config/paper_configs/resolution1024_without_hr_skip_connections.yaml'#os.environ.get('CONFIG_PATH')
#checkpoint = '/video-conf/scratch/pantea_experiments_tardy/resolution1024_without_hr_skip_connections/needle_drop_resolution1024_without_hr_skip_connections 09_04_22_20.18.58/00000069-checkpoint.pth.tar'#os.environ.get('CHECKPOINT_PATH', 'None')
#model = FirstOrderModel(config_path, checkpoint)
#for i in range(1):
#    zero_array = np.random.randint(0, 255, model.get_shape(), dtype=np.uint8)
#    zero_kps, src_index = model.extract_keypoints(zero_array)
#    model.update_source(src_index, zero_array, zero_kps)
#    zero_kps['source_index'] = src_index
#    model.predict(zero_kps)
#time_after_instantiation = time.perf_counter()
#print("Time to instantiate at time %s: %s",  datetime.datetime.now(), str(time_after_instantiation - time_before_instantiation))
#model.reset()
logger = logging.getLogger(__name__)

RTP_HISTORY_SIZE = 128
RTT_ALPHA = 0.85


class RTCRtpSender:
    """
    The :class:`RTCRtpSender` interface provides the ability to control and
    obtain details about how a particular :class:`MediaStreamTrack` is encoded
    and sent to a remote peer.

    :param trackOrKind: Either a :class:`MediaStreamTrack` instance or a
                         media kind (`'audio'` or `'video'`).
    :param transport: An :class:`RTCDtlsTransport`.
    """

    def __init__(self, trackOrKind: Union[MediaStreamTrack, str], transport, quantizer) -> None:
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
        self.__encoder: Optional[Encoder] = None
        self.__force_keyframe = False
        self.__quantizer = quantizer
        self.__loop = asyncio.get_event_loop()
        self.__mid: Optional[str] = None
        self.__rtp_exited = asyncio.Event()
        self.__rtp_header_extensions_map = rtp.HeaderExtensionsMap()
        self.__rtp_task: Optional[asyncio.Future[None]] = None
        self.__rtp_history: Dict[int, RtpPacket] = {}
        self.__rtcp_exited = asyncio.Event()
        self.__rtcp_task: Optional[asyncio.Future[None]] = None
        self.__rtx_payload_type: Optional[int] = None
        self.__rtx_sequence_number = 61495 #random16()
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
        self.__rtt = None

        self.__lsr_time_list = []
        self.__lsr_list = []
        self.__rtt_list = []

    @property
    def kind(self):
        return self.__kind

    @property
    def track(self) -> MediaStreamTrack:
        """
        The :class:`MediaStreamTrack` which is being handled by the sender.
        """
        return self.__track

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which media data for the track is
        transmitted.
        """
        return self.__transport

    @classmethod
    def getCapabilities(self, kind):
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

    def setTransport(self, transport) -> None:
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
            # Vibhaa: to change codec type to h264 - change this 0 to 2
            # one is some weird retransmission protocol
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

    async def stop(self):
        """
        Irreversibly stop the sender.
        """
        if self.__started:
            self.__transport._unregister_rtp_sender(self)
            self.__rtp_task.cancel()
            self.__rtcp_task.cancel()
            await asyncio.gather(self.__rtp_exited.wait(), self.__rtcp_exited.wait())

    async def _handle_rtcp_packet(self, packet):
        self.__log_debug("< RTCP %s arrival time:%d",
                packet, clock.current_ntp_time())
        
        if isinstance(packet, (RtcpRrPacket, RtcpSrPacket)):
            for report in filter(lambda x: x.ssrc == self._ssrc, packet.reports):
                # estimate round-trip time
                #if self.__lsr == report.lsr and report.dlsr:
                if report.lsr in self.__lsr_list and report.dlsr:
                    rtt = time.time() - self.__lsr_time_list[self.__lsr_list.index(report.lsr)] - (report.dlsr / 65536)
                    #print("estimated rtt is", rtt)
                    self.__log_debug("estimated rtt is %s, fraction_lost %d", rtt, report.fraction_lost)
                    #self.__rtt_list.append((rtt, report.packets_lost, report.fraction_lost))
                    #print(self.__rtt_list)
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
                self.__log_debug("dispatching retransmit %s", seq)
                await self._retransmit(seq)
        elif isinstance(packet, RtcpPsfbPacket) and packet.fmt == RTCP_PSFB_PLI:
            self._send_keyframe()
        elif isinstance(packet, RtcpPsfbPacket) and packet.fmt == RTCP_PSFB_APP:
            try:
                bitrate, ssrcs = unpack_remb_fci(packet.fci)
                if self._ssrc in ssrcs:
                    self.__log_debug(
                        "- receiver estimated maximum bitrate %d bps at time %s", bitrate, datetime.datetime.now()
                    )
                    if self.__encoder and hasattr(self.__encoder, "target_bitrate"):
                        self.__encoder.target_bitrate = bitrate
            except ValueError:
                pass

    async def _next_encoded_frame(self, codec: RTCRtpCodecParameters):
        # get frame
        frame = await self.__track.recv()

        # encode frame
        if self.__encoder is None:
            self.__encoder = get_encoder(codec)
        force_keyframe = self.__force_keyframe
        quantizer = self.__quantizer
        self.__force_keyframe = False
        self.__log_debug("encoding frame with force keyframe %s at time %s", 
                        force_keyframe, datetime.datetime.now())
        return await self.__loop.run_in_executor(
            None, self.__encoder.encode, frame, force_keyframe, quantizer
        )

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

            self.__log_debug("> %s retransmission of original %s", packet, sequence_number)
            packet_bytes = packet.serialize(self.__rtp_header_extensions_map)
            await self.transport._send_rtp(packet_bytes)

    def _send_keyframe(self) -> None:
        """
        Request the next frame to be a keyframe.
        """
        self.__force_keyframe = True
    
    def ml_predict(self, frame, counter):
        frame_array = frame.to_ndarray()
        frame_array = np.resize(frame_array, (1024, 1024, 3))
        kps, src_index = model.extract_keypoints(frame_array)
        model.update_source(src_index, frame_array, kps)
        kps['source_index'] = src_index
        predicted_frame = model.predict(kps)
        #print(predicted_frame, predicted_frame.shape)
        predicted_frame = av.VideoFrame.from_ndarray(np.array(predicted_frame))
        self.save_video_frame_as_image(predicted_frame, counter)
        return predicted_frame
    
    def save_video_frame_as_image(self, frame, counter):
        frame_array = frame.to_ndarray()
        im = Image.fromarray(frame_array)
        im.save(f"predicted_{counter}.jpeg")
 
    async def _run_rtp(self, codec: RTCRtpCodecParameters) -> None:
        self.__log_debug("- RTP started")

        sequence_number = 0 #random16()
        timestamp_origin = 0 #random32()
        try:
            counter = 0
            compression_sizes = []
            while True:
                if not self.__track:
                    await asyncio.sleep(0.02)
                    continue
#		# used if trying the compression experiments
#                frame = await self.__track.recv()
#                #print(frame)
#                delta = 0
#                counter += 1
#                revive_frame_num = 1800
#                if counter >= 100 and counter <= revive_frame_num - delta:
#                    #print("skipping encoding", counter)
#                    continue
#                elif counter == revive_frame_num + 1:
#                    self._send_keyframe()
#                    frame_array = frame.to_ndarray()
#                    print(frame_array.shape)
#                    if frame_array.shape[1] == 1024:
#                        #self._send_keyframe()
#                        print("Prediction is happening")
#                        original_pts = frame.pts
#                        original_time_base = frame.time_base
#                        loop = asyncio.get_running_loop()
#                        with concurrent.futures.ThreadPoolExecutor() as pool:
#                            frame = await loop.run_in_executor(pool, self.ml_predict, frame, counter)
#                        frame.pts = original_pts
#                        frame.time_base = original_time_base
#                        print("Prediction was successful! changed pts and time_base")
#                    else:
#                        continue
                payloads, timestamp = await self._next_encoded_frame(codec)
#                if counter < revive_frame_num + 10 and counter>= 99 and self.__track.kind == "video":
#                    if counter == 99:
#                        #self.save_video_frame_as_image(frame, counter)
#                        base_len = len(payloads)
#                    else:
#                        frame_array = frame.to_ndarray()
#                        if counter == 201:
#                            self.save_video_frame_as_image(frame, counter)
#                        if frame_array.shape[1] == 1024:
#                             print(delta, counter, base_len, len(payloads) - base_len)
#                frame_array = frame.to_ndarray()
#                if frame_array.shape[1] == 1024:
#                    compression_sizes.append(len(payloads))
#                    #print(compression_sizes)
#                    #print(self.__track.kind, counter, len(payloads))
#                self.__log_debug("frame %s is encoded with timestamp %s at time %s with len %s", 
#                                counter, timestamp, datetime.datetime.now(), len(payloads))
                old_timestamp = timestamp
                timestamp = uint32_add(timestamp_origin, timestamp)

                for i, payload in enumerate(payloads):
                    packet = RtpPacket(
                        payload_type=codec.payloadType,
                        sequence_number=sequence_number,
                        timestamp=timestamp,
                    )
                    packet.ssrc = self._ssrc
                    packet.payload = payload
                    packet.marker = (i == len(payloads) - 1) and 1 or 0

                    # set header extensions
                    packet.extensions.abs_send_time = (
                        clock.current_ntp_time() >> 14
                    ) & 0x00FFFFFF
                    packet.extensions.mid = self.__mid

                    # send packet
                    self.__log_debug("> RTP %s (encoded frame ts: %s) %s", packet, old_timestamp, 
                                    datetime.datetime.now())
                    self.__rtp_history[
                        packet.sequence_number % RTP_HISTORY_SIZE
                    ] = packet
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

        self.__log_debug("- RTP finished")
        self.__rtp_exited.set()

    async def _run_rtcp(self) -> None:
        self.__log_debug("- RTCP started")

        try:
            while True:
                # The interval between RTCP packets is varied randomly over the
                # range [0.5, 1.5] times the calculated interval.
                await asyncio.sleep(0.5 + random.random())

                # RTCP SR
                packets: List[AnyRtcpPacket] = [
                    RtcpSrPacket(
                        ssrc=self._ssrc,
                        sender_info=RtcpSenderInfo(
                            ntp_timestamp=self.__ntp_timestamp,
                            rtp_timestamp=self.__rtp_timestamp,
                            packet_count=self.__packet_count,
                            octet_count=self.__octet_count,
                        ),
                    )
                ]
                self.__lsr = ((self.__ntp_timestamp) >> 16) & 0xFFFFFFFF
                self.__lsr_time = time.time()
                self.__lsr_list.append(self.__lsr)
                self.__lsr_time_list.append(self.__lsr_time)
                
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

    async def _send_rtcp(self, packets: List[AnyRtcpPacket]) -> None:
        payload = b""
        for packet in packets:
            self.__log_debug("> RTCP %s ", packet)
            payload += bytes(packet)

        try:
            await self.transport._send_rtp(payload)
        except ConnectionError:
            pass

    def __log_debug(self, msg: str, *args) -> None:
        logger.debug(f"RTCRtpSender(%s) {msg}", self.__kind, *args)

    def __log_warning(self, msg: str, *args) -> None:
        logger.warning(f"RTCRtpsender(%s) {msg}", self.__kind, *args)
