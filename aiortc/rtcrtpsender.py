import asyncio
import logging
import random

from .codecs import get_encoder
from .exceptions import InvalidStateError
from .rtp import (RtcpByePacket, RtcpSdesPacket, RtcpSenderInfo,
                  RtcpSourceInfo, RtcpSrPacket, RtpPacket, datetime_to_ntp,
                  seq_plus_one, utcnow)
from .utils import first_completed, random32

logger = logging.getLogger('rtp')


class RTCRtpSender:
    """
    The :class:`RTCRtpSender` interface provides the ability to control and
    obtain details about how a particular :class:`MediaStreamTrack` is encoded
    and sent to a remote peer.

    :param: trackOrKind: Either a :class:`MediaStreamTrack` instance or a
                         media kind (`'audio'` or `'video'`).
    :param: transport: An :class:`RTCDtlsTransport`.
    """
    def __init__(self, trackOrKind, transport):
        if transport.state == 'closed':
            raise InvalidStateError

        if hasattr(trackOrKind, 'kind'):
            self._kind = trackOrKind.kind
            self._track = trackOrKind
        else:
            self._kind = trackOrKind
            self._track = None
        self._ssrc = random32()
        self.__rtp_exited = asyncio.Event()
        self.__rtcp_exited = asyncio.Event()
        self.__started = False
        self.__stopped = asyncio.Event()
        self.__transport = transport

        # stats
        self.__ntp_timestamp = 0
        self.__rtp_timestamp = 0
        self.__octet_count = 0
        self.__packet_count = 0

    @property
    def kind(self):
        return self._kind

    @property
    def track(self):
        """
        The :class:`MediaStreamTrack` which is being handled by the sender.
        """
        return self._track

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which media data for the track is
        transmitted.
        """
        return self.__transport

    def replaceTrack(self, track):
        self._track = track

    def setTransport(self, transport):
        self.__transport = transport

    async def send(self, parameters):
        """
        Attempt to set the parameters controlling the sending of media.

        :param: parameters: The :class:`RTCRtpParameters` for the sender.
        """
        if not self.__started:
            asyncio.ensure_future(self._run_rtp(parameters.codecs[0]))
            asyncio.ensure_future(self._run_rtcp())
            self.__started = True

    async def stop(self):
        """
        Irreversibly stop the sender.
        """
        self.__stopped.set()
        if self.__started:
            await asyncio.gather(
                self.__rtp_exited.wait(),
                self.__rtcp_exited.wait())

    async def _run_rtp(self, codec):
        self.__log_debug('- RTP started')
        loop = asyncio.get_event_loop()

        encoder = get_encoder(codec)
        packet = RtpPacket(payload_type=codec.payloadType)
        while not self.__stopped.is_set():
            if self._track:
                frame = await first_completed(self._track.recv(), self.__stopped.wait())
                if frame is True:
                    break
                packet.ssrc = self._ssrc
                payloads = await loop.run_in_executor(None, encoder.encode, frame)
                if not isinstance(payloads, list):
                    payloads = [payloads]
                for i, payload in enumerate(payloads):
                    packet.payload = payload
                    packet.marker = (i == len(payloads) - 1) and 1 or 0
                    try:
                        self.__log_debug('> %s', packet)
                        await self.transport._send_rtp(bytes(packet))
                    except ConnectionError:
                        self.__stopped.set()
                        break
                    self.__ntp_timestamp = datetime_to_ntp(utcnow())
                    self.__rtp_timestamp = packet.timestamp
                    self.__octet_count += len(payload)
                    self.__packet_count += 1
                    packet.sequence_number = seq_plus_one(packet.sequence_number)
                packet.timestamp += encoder.timestamp_increment
            else:
                await asyncio.sleep(0.02)

        self.__log_debug('- RTP finished')
        self.__rtp_exited.set()

    async def _run_rtcp(self):
        self.__log_debug('- RTCP started')

        while not self.__stopped.is_set():
            # The interval between RTCP packets is varied randomly over the
            # range [0.5, 1.5] times the calculated interval.
            sleep = 0.5 + random.random()
            result = await first_completed(asyncio.sleep(sleep), self.__stopped.wait())
            if result is True:
                break

            # RTCP SR
            packets = [RtcpSrPacket(
                ssrc=self._ssrc,
                sender_info=RtcpSenderInfo(
                    ntp_timestamp=self.__ntp_timestamp,
                    rtp_timestamp=self.__rtp_timestamp,
                    packet_count=self.__packet_count,
                    octet_count=self.__octet_count))]

            # RTCP SDES
            if self._cname is not None:
                packets.append(RtcpSdesPacket(chunks=[RtcpSourceInfo(
                    ssrc=self._ssrc,
                    items=[(1, self._cname.encode('utf8'))])]))

            await self._send_rtcp(packets)

        # RTCP BYE
        packet = RtcpByePacket(sources=[self._ssrc])
        await self._send_rtcp([packet])

        self.__log_debug('- RTCP finished')
        self.__rtcp_exited.set()

    async def _send_rtcp(self, packets):
        payload = b''
        for packet in packets:
            self.__log_debug('> %s', packet)
            payload += bytes(packet)

        try:
            await self.transport._send_rtp(payload)
        except ConnectionError:
            pass

    def __log_debug(self, msg, *args):
        logger.debug('sender(%s) ' + msg, self._kind, *args)
