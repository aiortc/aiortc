import asyncio
import datetime
import logging
import random
import time

from .codecs import get_decoder
from .exceptions import InvalidStateError
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamTrack
from .rtp import (RTP_SEQ_MODULO, RtcpReceiverInfo, RtcpRrPacket, RtcpSrPacket,
                  datetime_from_ntp, seq_gt)
from .stats import (RTCRemoteInboundRtpStreamStats,
                    RTCRemoteOutboundRtpStreamStats)
from .utils import first_completed

logger = logging.getLogger('rtp')


class LossCounter:
    def __init__(self, seq):
        self.base_seq = seq
        self.max_seq = seq
        self.cycles = 0
        self.packets_received = 1

        # fraction lost
        self._expected_prior = 0
        self._received_prior = 0

    def add(self, seq):
        self.packets_received += 1
        if seq_gt(seq, self.max_seq):
            if seq < self.max_seq:
                self.cycles += RTP_SEQ_MODULO
            self.max_seq = seq

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
    def packets_expected(self):
        return self.cycles + self.max_seq - self.base_seq + 1

    @property
    def packets_lost(self):
        return self.packets_expected - self.packets_received


class RemoteStreamTrack(MediaStreamTrack):
    def __init__(self, kind):
        self.kind = kind
        self._queue = asyncio.Queue()

    async def recv(self):
        return await self._queue.get()


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

        self._decoders = {}
        self._kind = kind
        self._jitter_buffer = JitterBuffer(capacity=32)
        self._track = None
        self.__rtcp_exited = asyncio.Event()
        self.__started = False
        self._stats = {}
        self.__stopped = asyncio.Event()
        self.__transport = transport

        # RTCP
        self._ssrc = None
        self.__lsr = None
        self.__lsr_stamp = None
        self.__remote_counter = None
        self.__remote_ssrc = None

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which the media for the receiver's
        track is received.
        """
        return self.__transport

    async def receive(self, parameters):
        """
        Attempt to set the parameters controlling the receiving of media.

        :param: parameters: The :class:`RTCRtpParameters` for the receiver.
        """
        if not self.__started:
            for codec in parameters.codecs:
                self._decoders[codec.payloadType] = get_decoder(codec)
            self.__transport._register_rtp_receiver(self, parameters)
            asyncio.ensure_future(self._run_rtcp())
            self.__started = True

    def setTransport(self, transport):
        self.__transport = transport

    async def stop(self):
        """
        Irreversibly stop the receiver.
        """
        if self.__started:
            self.__stopped.set()
            await self.__rtcp_exited.wait()

    async def _handle_rtcp_packet(self, packet):
        self.__log_debug('< %s', packet)

        if isinstance(packet, RtcpSrPacket):
            stats = RTCRemoteOutboundRtpStreamStats(
                # RTCStats
                timestamp=datetime.datetime.now(),
                type='remote-outbound-rtp',
                id=str(id(self)),
                # RTCStreamStats
                ssrc=packet.ssrc,
                kind=self._kind,
                transportId=str(id(self.transport)),
                # RTCSentRtpStreamStats
                packetsSent=packet.sender_info.packet_count,
                bytesSent=packet.sender_info.octet_count,
                # RTCRemoteOutboundRtpStreamStats
                localId='TODO',
                remoteTimestamp=datetime_from_ntp(packet.sender_info.ntp_timestamp)
            )
            self._stats[stats.type] = stats
            self.__lsr = ((packet.sender_info.ntp_timestamp) >> 16) & 0xffffffff
            self.__lsr_time = time.time()

        if isinstance(packet, (RtcpRrPacket, RtcpSrPacket)):
            for report in packet.reports:
                stats = RTCRemoteInboundRtpStreamStats(
                    # RTCStats
                    timestamp=datetime.datetime.now(),
                    type='remote-inbound-rtp',
                    id=str(id(self)),
                    # RTCStreamStats
                    ssrc=packet.ssrc,
                    kind=self._kind,
                    transportId=str(id(self.transport)),
                    # RTCReceivedRtpStreamStats
                    packetsReceived=0,  # FIXME: where do we get this?
                    packetsLost=report.packets_lost,
                    jitter=report.jitter,
                    # RTCRemoteInboundRtpStreamStats
                    localId='TODO',
                    roundTripTime=0,  # FIXME: where do we get this?
                    fractionLost=report.fraction_lost
                )
                self._stats[stats.type] = stats

    async def _handle_rtp_packet(self, packet):
        self.__log_debug('< %s', packet)
        if packet.payload_type in self._decoders:
            decoder = self._decoders[packet.payload_type]
            loop = asyncio.get_event_loop()

            # RTCP
            if self.__remote_ssrc is None:
                self.__remote_ssrc = packet.ssrc
                self.__remote_counter = LossCounter(packet.sequence_number)
            else:
                self.__remote_counter.add(packet.sequence_number)

            if self._kind == 'audio':
                # FIXME: audio should use the jitter buffer!
                audio_frame = await loop.run_in_executor(None, decoder.decode, packet.payload)
                await self._track._queue.put(audio_frame)
            else:
                # check if we have a complete video frame
                self._jitter_buffer.add(packet.payload, packet.sequence_number, packet.timestamp)
                payloads = []
                got_frame = False
                last_timestamp = None
                for count in range(self._jitter_buffer.capacity):
                    frame = self._jitter_buffer.peek(count)
                    if frame is None:
                        break
                    if last_timestamp is None:
                        last_timestamp = frame.timestamp
                    elif frame.timestamp != last_timestamp:
                        got_frame = True
                        break
                    payloads.append(frame.payload)

                if got_frame:
                    self._jitter_buffer.remove(count)
                    video_frames = await loop.run_in_executor(None, decoder.decode, payloads)
                    for video_frame in video_frames:
                        await self._track._queue.put(video_frame)

    async def _run_rtcp(self):
        self.__log_debug('- RTCP started')

        while not self.__stopped.is_set():
            # The interval between RTCP packets is varied randomly over the
            # range [0.5, 1.5] times the calculated interval.
            sleep = 0.5 + random.random()
            result = await first_completed(asyncio.sleep(sleep), self.__stopped.wait())
            if result is True:
                break

            # RTCP RR
            if self._ssrc is not None and self.__remote_ssrc is not None:
                lsr = 0
                dlsr = 0
                if self.__lsr is not None:
                    lsr = self.__lsr
                    dlsr = int((time.time() - self.__lsr_time) * 65536)

                packet = RtcpRrPacket(
                    ssrc=self._ssrc,
                    reports=[RtcpReceiverInfo(
                        ssrc=self.__remote_ssrc,
                        fraction_lost=self.__remote_counter.fraction_lost,
                        packets_lost=self.__remote_counter.packets_lost,
                        highest_sequence=self.__remote_counter.max_seq,
                        jitter=0,  # TODO
                        lsr=lsr,
                        dlsr=dlsr)])
                await self._send_rtcp(packet)

        self.__log_debug('- RTCP finished')
        self.__rtcp_exited.set()

    async def _send_rtcp(self, packet):
        self.__log_debug('> %s', packet)
        try:
            await self.transport._send_rtp(bytes(packet))
        except ConnectionError:
            pass

    def __log_debug(self, msg, *args):
        logger.debug('receiver(%s) ' + msg, self._kind, *args)
