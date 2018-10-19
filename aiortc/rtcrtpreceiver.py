import asyncio
import logging
import queue
import random
import threading
import time

from . import clock
from .codecs import depayload, get_decoder
from .exceptions import InvalidStateError
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamError, MediaStreamTrack
from .rate import RemoteBitrateEstimator
from .rtp import (RTCP_PSFB_APP, RTCP_PSFB_PLI, RTCP_RTPFB_NACK, RtcpByePacket,
                  RtcpPsfbPacket, RtcpReceiverInfo, RtcpRrPacket,
                  RtcpRtpfbPacket, RtcpSrPacket, clamp_packets_lost,
                  pack_remb_fci)
from .stats import (RTCInboundRtpStreamStats, RTCRemoteOutboundRtpStreamStats,
                    RTCStatsReport)
from .utils import uint16_add, uint16_gt

logger = logging.getLogger('rtp')


def decoder_worker(loop, input_q, output_q):
    codec_name = None
    decoder = None

    while True:
        task = input_q.get()
        if task is None:
            # inform the track that is has ended
            asyncio.run_coroutine_threadsafe(output_q.put(None), loop)
            break
        codec, encoded_frame = task

        if codec.name != codec_name:
            decoder = get_decoder(codec)
            codec_name = codec.name

        for frame in decoder.decode(encoded_frame):
            # pass the decoded frame to the track
            asyncio.run_coroutine_threadsafe(output_q.put(frame), loop)

    if decoder is not None:
        del decoder


class NackGenerator:
    def __init__(self, receiver):
        self.receiver = receiver
        self.max_seq = None
        self.missing = None
        self.ssrc = None

    async def add(self, packet):
        if packet.ssrc != self.ssrc:
            self.max_seq = packet.sequence_number
            self.missing = set()
            self.ssrc = packet.ssrc
            return

        if uint16_gt(packet.sequence_number, self.max_seq):
            # mark missing packets
            missed = 0
            seq = uint16_add(self.max_seq, 1)
            while uint16_gt(packet.sequence_number, seq):
                self.missing.add(seq)
                missed += 1
                seq = uint16_add(seq, 1)
            self.max_seq = packet.sequence_number

            # trigger a NACK if needed
            if missed:
                await self.receiver._send_rtcp_nack(self.ssrc, sorted(self.missing))
        else:
            self.missing.discard(packet.sequence_number)


class StreamStatistics:
    def __init__(self, ssrc, clockrate):
        self.base_seq = None
        self.max_seq = None
        self.cycles = 0
        self.packets_received = 0
        self.ssrc = ssrc

        # jitter
        self._clockrate = clockrate
        self._jitter_q4 = 0
        self._last_arrival = None
        self._last_timestamp = None

        # fraction lost
        self._expected_prior = 0
        self._received_prior = 0

    def add(self, packet):
        in_order = self.max_seq is None or uint16_gt(packet.sequence_number, self.max_seq)
        self.packets_received += 1

        if self.base_seq is None:
            self.base_seq = packet.sequence_number

        if in_order:
            arrival = int(time.time() * self._clockrate)

            if self.max_seq is not None and packet.sequence_number < self.max_seq:
                self.cycles += (1 << 16)
            self.max_seq = packet.sequence_number

            if packet.timestamp != self._last_timestamp and self.packets_received > 1:
                diff = abs((arrival - self._last_arrival) -
                           (packet.timestamp - self._last_timestamp))
                self._jitter_q4 += diff - ((self._jitter_q4 + 8) >> 4)

            self._last_arrival = arrival
            self._last_timestamp = packet.timestamp

    @property
    def fraction_lost(self):
        expected_interval = self.packets_expected - self._expected_prior
        self._expected_prior = self.packets_expected
        received_interval = self.packets_received - self._received_prior
        self._received_prior = self.packets_received
        lost_interval = expected_interval - received_interval
        if (expected_interval == 0 or lost_interval <= 0):
            return 0
        else:
            return (lost_interval << 8) // expected_interval

    @property
    def jitter(self):
        return self._jitter_q4 >> 4

    @property
    def packets_expected(self):
        return self.cycles + self.max_seq - self.base_seq + 1

    @property
    def packets_lost(self):
        return clamp_packets_lost(self.packets_expected - self.packets_received)


class RemoteStreamTrack(MediaStreamTrack):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self._queue = asyncio.Queue()

    async def recv(self):
        """
        Receive the next frame.
        """
        if self.readyState != 'live':
            raise MediaStreamError

        frame = await self._queue.get()
        if frame is None:
            self.stop()
            raise MediaStreamError
        return frame


class TimestampMapper:
    def __init__(self):
        self._last = None
        self._origin = None

    def map(self, timestamp):
        if self._origin is None:
            # first timestamp
            self._origin = timestamp
        elif timestamp < self._last:
            # RTP timestamp wrapped
            self._origin -= (1 << 32)

        self._last = timestamp
        return timestamp - self._origin


class RTCRtpReceiver:
    """
    The :class:`RTCRtpReceiver` interface manages the reception and decoding
    of data for a :class:`MediaStreamTrack`.

    :param: kind: The kind of media (`'audio'` or `'video'`).
    :param: transport: An :class:`RTCDtlsTransport`.
    """
    def __init__(self, kind, transport):
        if transport.state == 'closed':
            raise InvalidStateError

        self.__codecs = {}
        self.__decoder_queue = queue.Queue()
        self.__decoder_thread = None
        self.__kind = kind
        if kind == 'audio':
            self.__jitter_buffer = JitterBuffer(capacity=16, prefetch=4)
            self.__nack_generator = None
            self.__remote_bitrate_estimator = None
        else:
            self.__jitter_buffer = JitterBuffer(capacity=128)
            self.__nack_generator = NackGenerator(self)
            self.__remote_bitrate_estimator = RemoteBitrateEstimator()
        self._track = None
        self.__rtcp_exited = asyncio.Event()
        self.__rtcp_task = None
        self.__sender = None
        self.__started = False
        self.__stats = RTCStatsReport()
        self.__timestamp_mapper = TimestampMapper()
        self.__transport = transport

        # RTCP
        self._ssrc = None
        self.__lsr = None
        self.__lsr_time = None
        self.__remote_counter = None

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which the media for the receiver's
        track is received.
        """
        return self.__transport

    async def getStats(self):
        """
        Returns statistics about the RTP receiver.

        :rtype: :class:`RTCStatsReport`
        """
        if self.__remote_counter is not None:
            self.__stats.add(RTCInboundRtpStreamStats(
                # RTCStats
                timestamp=clock.current_datetime(),
                type='inbound-rtp',
                id='inbound-rtp_' + str(id(self)),
                # RTCStreamStats
                ssrc=self.__remote_counter.ssrc,
                kind=self.__kind,
                transportId=self.transport._stats_id,
                # RTCReceivedRtpStreamStats
                packetsReceived=self.__remote_counter.packets_received,
                packetsLost=self.__remote_counter.packets_lost,
                jitter=self.__remote_counter.jitter,
                # RTPInboundRtpStreamStats
            ))
        self.__stats.update(self.transport._get_stats())

        return self.__stats

    async def receive(self, parameters):
        """
        Attempt to set the parameters controlling the receiving of media.

        :param: parameters: The :class:`RTCRtpParameters` for the receiver.
        """
        if not self.__started:
            for codec in parameters.codecs:
                self.__codecs[codec.payloadType] = codec

            # start decoder thread
            self.__decoder_thread = threading.Thread(
                target=decoder_worker,
                name=self.__kind + '-decoder',
                args=(asyncio.get_event_loop(), self.__decoder_queue, self._track._queue))
            self.__decoder_thread.start()

            self.__transport._register_rtp_receiver(self, parameters)
            self.__rtcp_task = asyncio.ensure_future(self._run_rtcp())
            self.__started = True

    def setTransport(self, transport):
        self.__transport = transport

    async def stop(self):
        """
        Irreversibly stop the receiver.
        """
        if self.__started:
            self.__transport._unregister_rtp_receiver(self)
            self.__stop_decoder()
            self.__rtcp_task.cancel()
            await self.__rtcp_exited.wait()

        # break reference cycle
        self.__nack_generator = None

    def _handle_disconnect(self):
        self.__stop_decoder()

    async def _handle_rtcp_packet(self, packet):
        self.__log_debug('< %s', packet)

        if isinstance(packet, RtcpSrPacket):
            self.__stats.add(RTCRemoteOutboundRtpStreamStats(
                # RTCStats
                timestamp=clock.current_datetime(),
                type='remote-outbound-rtp',
                id='remote-outbound-rtp_' + str(id(self)),
                # RTCStreamStats
                ssrc=packet.ssrc,
                kind=self.__kind,
                transportId=self.transport._stats_id,
                # RTCSentRtpStreamStats
                packetsSent=packet.sender_info.packet_count,
                bytesSent=packet.sender_info.octet_count,
                # RTCRemoteOutboundRtpStreamStats
                remoteTimestamp=clock.datetime_from_ntp(packet.sender_info.ntp_timestamp)
            ))
            self.__lsr = ((packet.sender_info.ntp_timestamp) >> 16) & 0xffffffff
            self.__lsr_time = time.time()
        elif isinstance(packet, RtcpByePacket):
            self.__stop_decoder()

        # FIXME: could this be done at the DTLS level?
        if self.__sender:
            await self.__sender._handle_rtcp_packet(packet)

    async def _handle_rtp_packet(self, packet, arrival_time_ms):
        self.__log_debug('< %s', packet)

        # feed bitrate estimator
        if self.__remote_bitrate_estimator is not None:
            if packet.extensions.abs_send_time is not None:
                remb = self.__remote_bitrate_estimator.add(
                    abs_send_time=packet.extensions.abs_send_time,
                    arrival_time_ms=arrival_time_ms,
                    payload_size=len(packet.payload) + packet.padding_size,
                    ssrc=packet.ssrc,
                )
                if remb is not None:
                    # send Receiver Estimated Maximum Bitrate feedback
                    rtcp_packet = RtcpPsfbPacket(fmt=RTCP_PSFB_APP, ssrc=self._ssrc, media_ssrc=0)
                    rtcp_packet.fci = pack_remb_fci(*remb)
                    await self._send_rtcp(rtcp_packet)

        if packet.payload_type in self.__codecs:
            codec = self.__codecs[packet.payload_type]

            # feed RTCP statistics
            if self.__remote_counter is None or self.__remote_counter.ssrc != packet.ssrc:
                self.__remote_counter = StreamStatistics(packet.ssrc, codec.clockRate)
            self.__remote_counter.add(packet)

            # parse codec-specific information
            if packet.payload:
                packet._data = depayload(codec, packet.payload)
            else:
                packet._data = b''

            # send NACKs for any missing any packets
            if self.__nack_generator is not None:
                await self.__nack_generator.add(packet)

            # try to re-assemble encoded frame
            encoded_frame = self.__jitter_buffer.add(packet)

            # if we have a complete encoded frame, decode it
            if encoded_frame is not None and self.__decoder_thread:
                encoded_frame.timestamp = self.__timestamp_mapper.map(encoded_frame.timestamp)
                self.__decoder_queue.put((codec, encoded_frame))

    async def _run_rtcp(self):
        self.__log_debug('- RTCP started')

        try:
            while True:
                # The interval between RTCP packets is varied randomly over the
                # range [0.5, 1.5] times the calculated interval.
                await asyncio.sleep(0.5 + random.random())

                # RTCP RR
                if self._ssrc is not None and self.__remote_counter is not None:
                    lsr = 0
                    dlsr = 0
                    if self.__lsr is not None:
                        lsr = self.__lsr
                        delay = time.time() - self.__lsr_time
                        if delay > 0 and delay < 65536:
                            dlsr = int(delay * 65536)

                    packet = RtcpRrPacket(
                        ssrc=self._ssrc,
                        reports=[RtcpReceiverInfo(
                            ssrc=self.__remote_counter.ssrc,
                            fraction_lost=self.__remote_counter.fraction_lost,
                            packets_lost=self.__remote_counter.packets_lost,
                            highest_sequence=self.__remote_counter.max_seq,
                            jitter=self.__remote_counter.jitter,
                            lsr=lsr,
                            dlsr=dlsr)])
                    await self._send_rtcp(packet)
        except asyncio.CancelledError:
            pass

        self.__log_debug('- RTCP finished')
        self.__rtcp_exited.set()

    async def _send_rtcp(self, packet):
        self.__log_debug('> %s', packet)
        try:
            await self.transport._send_rtp(bytes(packet))
        except ConnectionError:
            pass

    async def _send_rtcp_nack(self, media_ssrc, lost):
        """
        Send an RTCP packet to report missing RTP packets.
        """
        if self._ssrc is not None:
            packet = RtcpRtpfbPacket(fmt=RTCP_RTPFB_NACK, ssrc=self._ssrc, media_ssrc=media_ssrc)
            packet.lost = lost
            await self._send_rtcp(packet)

    async def _send_rtcp_pli(self, media_ssrc):
        """
        Send an RTCP packet to report picture loss.
        """
        if self._ssrc is not None:
            packet = RtcpPsfbPacket(fmt=RTCP_PSFB_PLI, ssrc=self._ssrc, media_ssrc=media_ssrc)
            await self._send_rtcp(packet)

    def _set_sender(self, sender):
        self.__sender = sender

    def __stop_decoder(self):
        """
        Stop the decoder thread, which will in turn stop the track.
        """
        if self.__decoder_thread:
            self.__decoder_queue.put(None)
            self.__decoder_thread.join()
            self.__decoder_thread = None

    def __log_debug(self, msg, *args):
        logger.debug('receiver(%s) ' + msg, self.__kind, *args)
