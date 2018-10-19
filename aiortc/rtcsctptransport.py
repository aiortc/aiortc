import asyncio
import enum
import hmac
import logging
import math
import os
import time
from struct import pack, unpack

import attr
import crcmod.predefined
from pyee import EventEmitter

from .exceptions import InvalidStateError
from .rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from .utils import random32, uint16_add, uint16_gte

crc32c = crcmod.predefined.mkPredefinedCrcFun('crc-32c')
logger = logging.getLogger('sctp')

# local constants
COOKIE_LENGTH = 24
COOKIE_LIFETIME = 60
MAX_OUTBOUND_QUEUE = 100
MAX_STREAMS = 65535
USERDATA_MAX_LENGTH = 1200

# protocol constants
SCTP_CAUSE_INVALID_STREAM = 0x0001
SCTP_CAUSE_STALE_COOKIE = 0x0003

SCTP_DATA_LAST_FRAG = 0x01
SCTP_DATA_FIRST_FRAG = 0x02
SCTP_DATA_UNORDERED = 0x04

SCTP_MAX_ASSOCIATION_RETRANS = 10
SCTP_MAX_BURST = 4
SCTP_MAX_INIT_RETRANS = 8
SCTP_RTO_ALPHA = 1 / 8
SCTP_RTO_BETA = 1 / 4
SCTP_RTO_INITIAL = 3
SCTP_RTO_MIN = 1
SCTP_RTO_MAX = 60
SCTP_TSN_MODULO = 2 ** 32

RECONFIG_CHUNK = 130
RECONFIG_MAX_STREAMS = 135

# parameters
SCTP_STATE_COOKIE = 0x0007
SCTP_STR_RESET_OUT_REQUEST = 0x000d
SCTP_STR_RESET_RESPONSE = 0x0010
SCTP_STR_RESET_ADD_OUT_STREAMS = 0x0011
SCTP_SUPPORTED_CHUNK_EXT = 0x8008

# data channel constants
DATA_CHANNEL_ACK = 2
DATA_CHANNEL_OPEN = 3

DATA_CHANNEL_RELIABLE = 0x00
DATA_CHANNEL_RELIABLE_UNORDERED = 0x80

WEBRTC_DCEP = 50
WEBRTC_STRING = 51
WEBRTC_BINARY = 53
WEBRTC_STRING_EMPTY = 56
WEBRTC_BINARY_EMPTY = 57


def chunk_type(chunk):
    return chunk.__class__.__name__


def decode_params(body):
    params = []
    pos = 0
    while pos <= len(body) - 4:
        param_type, param_length = unpack('!HH', body[pos:pos + 4])
        params.append((param_type, body[pos + 4:pos + param_length]))
        pos += param_length + padl(param_length)
    return params


def encode_params(params):
    body = b''
    padding = b''
    for param_type, param_value in params:
        param_length = len(param_value) + 4
        body += padding
        body += pack('!HH', param_type, param_length) + param_value
        padding = b'\x00' * padl(param_length)
    return body


def padl(l):
    return 4 * ((l + 3) // 4) - l


def swapl(i):
    return unpack("<I", pack(">I", i))[0]


def tsn_gt(a, b):
    """
    Return True if tsn a is greater than b.
    """
    half_mod = (1 << 31)
    return (((a < b) and ((b - a) > half_mod)) or
            ((a > b) and ((a - b) < half_mod)))


def tsn_gte(a, b):
    """
    Return True if tsn a is greater than or equal to b.
    """
    return (a == b) or tsn_gt(a, b)


def tsn_minus_one(a):
    return (a - 1) % SCTP_TSN_MODULO


def tsn_plus_one(a):
    return (a + 1) % SCTP_TSN_MODULO


class Chunk:
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        self.body = body

    def __bytes__(self):
        body = self.body
        data = pack('!BBH', self.type, self.flags, len(body) + 4) + body
        data += b'\x00' * padl(len(body))
        return data

    def __repr__(self):
        return '%s(flags=%d)' % (chunk_type(self), self.flags)

    @property
    def type(self):
        for k, cls in CHUNK_TYPES.items():
            if isinstance(self, cls):
                return k


class BaseParamsChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        if body:
            self.params = decode_params(body)
        else:
            self.params = []

    @property
    def body(self):
        return encode_params(self.params)


class AbortChunk(BaseParamsChunk):
    pass


class CookieAckChunk(Chunk):
    pass


class CookieEchoChunk(Chunk):
    pass


class DataChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        if body:
            (self.tsn, self.stream_id, self.stream_seq, self.protocol) = unpack('!LHHL', body[0:12])
            self.user_data = body[12:]
        else:
            self.tsn = 0
            self.stream_id = 0
            self.stream_seq = 0
            self.protocol = 0
            self.user_data = b''

    @property
    def body(self):
        body = pack('!LHHL', self.tsn, self.stream_id, self.stream_seq, self.protocol)
        body += self.user_data
        return body

    def __repr__(self):
        return 'DataChunk(flags=%d, tsn=%d, stream_id=%d, stream_seq=%d)' % (
            self.flags, self.tsn, self.stream_id, self.stream_seq)


class ErrorChunk(BaseParamsChunk):
    pass


class HeartbeatChunk(BaseParamsChunk):
    pass


class HeartbeatAckChunk(BaseParamsChunk):
    pass


class BaseInitChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        if body:
            (self.initiate_tag, self.advertised_rwnd, self.outbound_streams,
             self.inbound_streams, self.initial_tsn) = unpack('!LLHHL', body[0:16])
            self.params = decode_params(body[16:])
        else:
            self.initiate_tag = 0
            self.advertised_rwnd = 0
            self.outbound_streams = 0
            self.inbound_streams = 0
            self.initial_tsn = 0
            self.params = []

    @property
    def body(self):
        body = pack(
            '!LLHHL', self.initiate_tag, self.advertised_rwnd, self.outbound_streams,
            self.inbound_streams, self.initial_tsn)
        body += encode_params(self.params)
        return body


class InitChunk(BaseInitChunk):
    pass


class InitAckChunk(BaseInitChunk):
    pass


class ReconfigChunk(BaseParamsChunk):
    pass


class SackChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        self.gaps = []
        self.duplicates = []
        if body:
            self.cumulative_tsn, self.advertised_rwnd, nb_gaps, nb_duplicates = unpack(
                '!LLHH', body[0:12])
            pos = 12
            for i in range(nb_gaps):
                self.gaps.append(unpack('!HH', body[pos:pos + 4]))
                pos += 4
            for i in range(nb_duplicates):
                self.duplicates.append(unpack('!L', body[pos:pos + 4])[0])
                pos += 4
        else:
            self.cumulative_tsn = 0
            self.advertised_rwnd = 0

    @property
    def body(self):
        body = pack('!LLHH', self.cumulative_tsn, self.advertised_rwnd,
                    len(self.gaps), len(self.duplicates))
        for gap in self.gaps:
            body += pack('!HH', *gap)
        for tsn in self.duplicates:
            body += pack('!L', tsn)
        return body

    def __repr__(self):
        return 'SackChunk(flags=%d, advertised_rwnd=%d, cumulative_tsn=%d, gaps=%s)' % (
            self.flags, self.advertised_rwnd, self.cumulative_tsn, self.gaps)


class ShutdownChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        if body:
            self.cumulative_tsn = unpack('!L', body[0:4])[0]
        else:
            self.cumulative_tsn = 0

    @property
    def body(self):
        return pack('!L', self.cumulative_tsn)

    def __repr__(self):
        return 'ShutdownChunk(flags=%d, cumulative_tsn=%d)' % (
            self.flags, self.cumulative_tsn)


class ShutdownAckChunk(Chunk):
    pass


class ShutdownCompleteChunk(Chunk):
    pass


CHUNK_TYPES = {
    0: DataChunk,
    1: InitChunk,
    2: InitAckChunk,
    3: SackChunk,
    4: HeartbeatChunk,
    5: HeartbeatAckChunk,
    6: AbortChunk,
    7: ShutdownChunk,
    8: ShutdownAckChunk,
    9: ErrorChunk,
    10: CookieEchoChunk,
    11: CookieAckChunk,
    14: ShutdownCompleteChunk,
    130: ReconfigChunk,
}


class Packet:
    def __init__(self, source_port, destination_port, verification_tag, chunks):
        self.source_port = source_port
        self.destination_port = destination_port
        self.verification_tag = verification_tag
        self.chunks = chunks

    def __bytes__(self):
        checksum = 0
        data = pack(
            '!HHLL',
            self.source_port,
            self.destination_port,
            self.verification_tag,
            checksum)
        for chunk in self.chunks:
            data += bytes(chunk)

        # calculate checksum
        checksum = swapl(crc32c(data))
        return data[0:8] + pack('!L', checksum) + data[12:]

    @classmethod
    def parse(cls, data):
        if len(data) < 12:
            raise ValueError('SCTP packet length is less than 12 bytes')

        source_port, destination_port, verification_tag, checksum = unpack(
            '!HHLL', data[0:12])

        # verify checksum
        check_data = data[0:8] + b'\x00\x00\x00\x00' + data[12:]
        if checksum != swapl(crc32c(check_data)):
            raise ValueError('SCTP packet has invalid checksum')

        packet = cls(
            source_port=source_port,
            destination_port=destination_port,
            verification_tag=verification_tag,
            chunks=[])

        pos = 12
        while pos <= len(data) - 4:
            chunk_type, chunk_flags, chunk_length = unpack('!BBH', data[pos:pos + 4])
            chunk_body = data[pos + 4:pos + chunk_length]
            chunk_cls = CHUNK_TYPES.get(chunk_type)
            if chunk_cls:
                packet.chunks.append(chunk_cls(
                    flags=chunk_flags,
                    body=chunk_body))
            pos += chunk_length + padl(chunk_length)
        return packet


# RFC 6525

@attr.s
class StreamResetOutgoingParam:
    request_sequence = attr.ib()
    response_sequence = attr.ib()
    last_tsn = attr.ib()
    streams = attr.ib(default=attr.Factory(list))

    def __bytes__(self):
        data = pack(
            '!LLL',
            self.request_sequence,
            self.response_sequence,
            self.last_tsn)
        for stream in self.streams:
            data += pack('!H', stream)
        return data

    @classmethod
    def parse(cls, data):
        request_sequence, response_sequence, last_tsn = unpack('!LLL', data[0:12])
        streams = []
        for pos in range(12, len(data), 2):
            streams.append(unpack('!H', data[pos:pos + 2])[0])
        return cls(
            request_sequence=request_sequence,
            response_sequence=response_sequence,
            last_tsn=last_tsn,
            streams=streams)


@attr.s
class StreamAddOutgoingParam:
    request_sequence = attr.ib()
    new_streams = attr.ib()

    def __bytes__(self):
        data = pack(
            '!LHH',
            self.request_sequence,
            self.new_streams,
            0)
        return data

    @classmethod
    def parse(cls, data):
        request_sequence, new_streams, reserved = unpack('!LHH', data[0:8])
        return cls(
            request_sequence=request_sequence,
            new_streams=new_streams)


@attr.s
class StreamResetResponseParam:
    response_sequence = attr.ib()
    result = attr.ib()

    def __bytes__(self):
        return pack('!LL', self.response_sequence, self.result)

    @classmethod
    def parse(cls, data):
        response_sequence, result = unpack('!LL', data[0:8])
        return cls(response_sequence=response_sequence, result=result)


RECONFIG_PARAM_TYPES = {
    13: StreamResetOutgoingParam,
    16: StreamResetResponseParam,
    17: StreamAddOutgoingParam
}


class InboundStream:
    def __init__(self):
        self.reassembly = []
        self.sequence_number = 0

    def add_chunk(self, chunk):
        pos = None

        # should never happen, this would mean receiving a chunk
        # for a message that has already been fully re-assembled
        assert uint16_gte(chunk.stream_seq, self.sequence_number)

        for i, rchunk in enumerate(self.reassembly):
            # should never happen, the chunk should have been eliminated
            # as a duplicate when _mark_received() is called
            assert rchunk.tsn != chunk.tsn

            if tsn_gt(rchunk.tsn, chunk.tsn):
                pos = i
                break
        if pos is None:
            pos = len(self.reassembly)

        self.reassembly.insert(pos, chunk)

    def pop_messages(self):
        pos = 0
        while pos < len(self.reassembly):
            chunk = self.reassembly[pos]
            if chunk.stream_seq != self.sequence_number:
                break

            if not pos:
                if not (chunk.flags & SCTP_DATA_FIRST_FRAG):
                    break
                expected_tsn = chunk.tsn
                user_data = chunk.user_data
            else:
                if chunk.tsn != expected_tsn:
                    break
                user_data += chunk.user_data

            if (chunk.flags & SCTP_DATA_LAST_FRAG):
                self.reassembly = self.reassembly[pos + 1:]
                self.sequence_number = uint16_add(self.sequence_number, 1)
                pos = 0
                yield (chunk.stream_id, chunk.protocol, user_data)
            else:
                pos += 1

            expected_tsn = tsn_plus_one(expected_tsn)


@attr.s
class RTCSctpCapabilities:
    """
    The :class:`RTCSctpCapabilities` dictionary provides information about the
    capabilities of the :class:`RTCSctpTransport`.
    """
    maxMessageSize = attr.ib()
    """
    The maximum size of data that the implementation can send or
    0 if the implementation can handle messages of any size.
    """


class RTCSctpTransport(EventEmitter):
    """
    The :class:`RTCSctpTransport` interface includes information relating to
    Stream Control Transmission Protocol (SCTP) transport.

    :param: transport: An :class:`RTCDtlsTransport`.
    """
    def __init__(self, transport, port=5000):
        if transport.state == 'closed':
            raise InvalidStateError

        super().__init__()
        self._association_state = self.State.CLOSED
        self.__transport = transport
        self._started = False
        self.__state = 'new'

        self._loop = asyncio.get_event_loop()
        self._hmac_key = os.urandom(16)

        self._local_extensions = [RECONFIG_CHUNK]
        self._local_port = port
        self._local_verification_tag = random32()

        self._remote_extensions = []
        self._remote_port = None
        self._remote_verification_tag = 0

        # inbound
        self._advertised_rwnd = 131072
        self._inbound_streams = {}
        self._inbound_streams_count = 0
        self._inbound_streams_max = MAX_STREAMS
        self._last_received_tsn = None
        self._sack_duplicates = []
        self._sack_misordered = set()
        self._sack_needed = False

        # outbound
        self._cwnd = 3 * USERDATA_MAX_LENGTH
        self._fast_recovery_exit = None
        self._fast_recovery_transmit = False
        self._flight_size = 0
        self._local_tsn = random32()
        self._last_sacked_tsn = tsn_minus_one(self._local_tsn)
        self._outbound_queue = []
        self._outbound_queue_pos = 0
        self._outbound_stream_seq = {}
        self._outbound_streams_count = MAX_STREAMS
        self._partial_bytes_acked = 0

        # reconfiguration
        self._reconfig_queue = []
        self._reconfig_request = None
        self._reconfig_request_seq = self._local_tsn
        self._reconfig_response_seq = 0

        # rtt calculation
        self._srtt = None
        self._rttvar = None

        # timers
        self._rto = SCTP_RTO_INITIAL
        self._t1_handle = None
        self._t2_handle = None
        self._t3_handle = None

        # data channels
        self._data_channel_id = None
        self._data_channel_queue = []
        self._data_channels = {}

    @property
    def is_server(self):
        return self.transport.transport.role != 'controlling'

    @property
    def port(self):
        """
        The local SCTP port number used for data channels.
        """
        return self._local_port

    @property
    def state(self):
        """
        The current state of the SCTP transport.
        """
        return self.__state

    @property
    def transport(self):
        """
        The :class:`RTCDtlsTransport` over which SCTP data is transmitted.
        """
        return self.__transport

    @classmethod
    def getCapabilities(cls):
        """
        Retrieve the capabilities of the transport.

        :rtype: RTCSctpCapabilities
        """
        return RTCSctpCapabilities(maxMessageSize=65536)

    def setTransport(self, transport):
        self.__transport = transport

    async def start(self, remoteCaps, remotePort):
        """
        Start the transport.
        """
        if not self._started:
            self._started = True
            self.__state = 'connecting'
            self._remote_port = remotePort

            # initialise local channel ID counter
            if self.is_server:
                self._data_channel_id = 0
            else:
                self._data_channel_id = 1

            self.__transport._register_data_receiver(self)
            if not self.is_server:
                await self._init()

    async def stop(self):
        """
        Stop the transport.
        """
        if self._association_state != self.State.CLOSED:
            await self._abort()
        self.__transport._unregister_data_receiver(self)
        self._set_state(self.State.CLOSED)

    async def _abort(self):
        """
        Abort the association.
        """
        chunk = AbortChunk()
        try:
            await self._send_chunk(chunk)
        except ConnectionError:
            pass

    async def _init(self):
        """
        Initialize the association.
        """
        chunk = InitChunk()
        chunk.initiate_tag = self._local_verification_tag
        chunk.advertised_rwnd = self._advertised_rwnd
        chunk.outbound_streams = self._outbound_streams_count
        chunk.inbound_streams = self._inbound_streams_max
        chunk.initial_tsn = self._local_tsn
        self._set_extensions(chunk.params)
        await self._send_chunk(chunk)

        # start T1 timer and enter COOKIE-WAIT state
        self._t1_start(chunk)
        self._set_state(self.State.COOKIE_WAIT)

    def _flight_size_decrease(self, chunk):
        self._flight_size = max(0, self._flight_size - chunk._book_size)

    def _flight_size_increase(self, chunk):
        self._flight_size += chunk._book_size

    def _get_extensions(self, params):
        """
        Gets what extensions are supported by the remote party.
        """
        for k, v in params:
            if k == SCTP_SUPPORTED_CHUNK_EXT:
                self._remote_extensions = list(v)

    def _set_extensions(self, params):
        """
        Sets what extensions are supported by the local party.
        """
        params.append((SCTP_SUPPORTED_CHUNK_EXT, bytes(self._local_extensions)))

    def _get_timestamp(self):
        return int(time.time())

    async def _handle_data(self, data):
        """
        Handle data received from the network.
        """
        try:
            packet = Packet.parse(data)
        except ValueError:
            return

        # is this an init?
        init_chunk = len([x for x in packet.chunks if isinstance(x, InitChunk)])
        if init_chunk:
            assert len(packet.chunks) == 1
            expected_tag = 0
        else:
            expected_tag = self._local_verification_tag

        # verify tag
        if packet.verification_tag != expected_tag:
            self.__log_debug('Bad verification tag %d vs %d',
                             packet.verification_tag, expected_tag)
            return

        # handle chunks
        for chunk in packet.chunks:
            await self._receive_chunk(chunk)

        # send SACK if needed
        if self._sack_needed:
            await self._send_sack()

    def _mark_received(self, tsn):
        """
        Mark a data TSN as received.
        """
        # it's a duplicate
        if tsn_gte(self._last_received_tsn, tsn) or tsn in self._sack_misordered:
            self._sack_duplicates.append(tsn)
            return True

        # consolidate misordered entries
        self._sack_misordered.add(tsn)
        for tsn in sorted(self._sack_misordered):
            if tsn == tsn_plus_one(self._last_received_tsn):
                self._last_received_tsn = tsn
            else:
                break

        # filter out obsolete entries
        def is_obsolete(x):
            return tsn_gt(x, self._last_received_tsn)
        self._sack_duplicates = list(filter(is_obsolete, self._sack_duplicates))
        self._sack_misordered = set(filter(is_obsolete, self._sack_misordered))

    async def _receive(self, stream_id, pp_id, data):
        """
        Receive data stream -> ULP.
        """
        await self._data_channel_receive(stream_id, pp_id, data)

    async def _receive_chunk(self, chunk):
        """
        Handle an incoming chunk.
        """
        self.__log_debug('< %s', chunk)

        # server
        if isinstance(chunk, InitChunk) and self.is_server:
            self._last_received_tsn = tsn_minus_one(chunk.initial_tsn)
            self._reconfig_response_seq = tsn_minus_one(chunk.initial_tsn)
            self._remote_verification_tag = chunk.initiate_tag
            self._ssthresh = chunk.advertised_rwnd
            self._get_extensions(chunk.params)

            self.__log_debug('- Peer supports %d outbound streams, %d max inbound streams',
                             chunk.outbound_streams, chunk.inbound_streams)
            self._inbound_streams_count = min(chunk.outbound_streams, self._inbound_streams_max)
            self._outbound_streams_count = min(self._outbound_streams_count, chunk.inbound_streams)

            ack = InitAckChunk()
            ack.initiate_tag = self._local_verification_tag
            ack.advertised_rwnd = self._advertised_rwnd
            ack.outbound_streams = self._outbound_streams_count
            ack.inbound_streams = self._inbound_streams_max
            ack.initial_tsn = self._local_tsn
            self._set_extensions(ack.params)

            # generate state cookie
            cookie = pack('!L', self._get_timestamp())
            cookie += hmac.new(self._hmac_key, cookie, 'sha1').digest()
            ack.params.append((SCTP_STATE_COOKIE, cookie))
            await self._send_chunk(ack)
        elif isinstance(chunk, CookieEchoChunk) and self.is_server:
            # check state cookie MAC
            cookie = chunk.body
            if (len(cookie) != COOKIE_LENGTH or
               hmac.new(self._hmac_key, cookie[0:4], 'sha1').digest() != cookie[4:]):
                self.__log_debug('x State cookie is invalid')
                return

            # check state cookie lifetime
            now = self._get_timestamp()
            stamp = unpack('!L', cookie[0:4])[0]
            if stamp < now - COOKIE_LIFETIME or stamp > now:
                self.__log_debug('x State cookie has expired')
                error = ErrorChunk()
                error.params.append((SCTP_CAUSE_STALE_COOKIE, b'\x00' * 8))
                await self._send_chunk(error)
                return

            ack = CookieAckChunk()
            await self._send_chunk(ack)
            self._set_state(self.State.ESTABLISHED)

        # client
        elif isinstance(chunk, InitAckChunk) and self._association_state == self.State.COOKIE_WAIT:
            # cancel T1 timer and process chunk
            self._t1_cancel()
            self._last_received_tsn = tsn_minus_one(chunk.initial_tsn)
            self._reconfig_response_seq = tsn_minus_one(chunk.initial_tsn)
            self._remote_verification_tag = chunk.initiate_tag
            self._ssthresh = chunk.advertised_rwnd
            self._get_extensions(chunk.params)

            self.__log_debug('- Peer supports %d outbound streams, %d max inbound streams',
                             chunk.outbound_streams, chunk.inbound_streams)
            self._inbound_streams_count = min(chunk.outbound_streams, self._inbound_streams_max)
            self._outbound_streams_count = min(self._outbound_streams_count, chunk.inbound_streams)

            echo = CookieEchoChunk()
            for k, v in chunk.params:
                if k == SCTP_STATE_COOKIE:
                    echo.body = v
                    break
            await self._send_chunk(echo)

            # start T1 timer and enter COOKIE-ECHOED state
            self._t1_start(echo)
            self._set_state(self.State.COOKIE_ECHOED)
        elif (isinstance(chunk, CookieAckChunk) and
              self._association_state == self.State.COOKIE_ECHOED):
            # cancel T1 timer and enter ESTABLISHED state
            self._t1_cancel()
            self._set_state(self.State.ESTABLISHED)
        elif (isinstance(chunk, ErrorChunk) and
              self._association_state in [self.State.COOKIE_WAIT, self.State.COOKIE_ECHOED]):
            self._t1_cancel()
            self._set_state(self.State.CLOSED)
            self.__log_debug('x Could not establish association')
            return

        # common
        elif isinstance(chunk, DataChunk):
            await self._receive_data_chunk(chunk)
        elif isinstance(chunk, SackChunk):
            await self._receive_sack_chunk(chunk)
        elif isinstance(chunk, HeartbeatChunk):
            ack = HeartbeatAckChunk()
            ack.params = chunk.params
            await self._send_chunk(ack)
        elif isinstance(chunk, AbortChunk):
            self.__log_debug('x Association was aborted by remote party')
            self._set_state(self.State.CLOSED)
        elif isinstance(chunk, ShutdownChunk):
            self._t2_cancel()
            self._set_state(self.State.SHUTDOWN_RECEIVED)
            ack = ShutdownAckChunk()
            await self._send_chunk(ack)
            self._t2_start(ack)
            self._set_state(self.State.SHUTDOWN_ACK_SENT)
        elif (isinstance(chunk, ShutdownCompleteChunk) and
              self._association_state == self.State.SHUTDOWN_ACK_SENT):
            self._t2_cancel()
            self._set_state(self.State.CLOSED)
        elif (isinstance(chunk, ReconfigChunk) and
              self._association_state == self.State.ESTABLISHED):
            for param in chunk.params:
                cls = RECONFIG_PARAM_TYPES.get(param[0])
                if cls:
                    await self._receive_reconfig_param(cls.parse(param[1]))

    async def _receive_data_chunk(self, chunk):
        """
        Handle a DATA chunk.
        """
        self._sack_needed = True

        # mark as received
        if self._mark_received(chunk.tsn):
            return

        # find stream
        if chunk.stream_id not in self._inbound_streams:
            self._inbound_streams[chunk.stream_id] = InboundStream()
        inbound_stream = self._inbound_streams[chunk.stream_id]

        # defragment data
        inbound_stream.add_chunk(chunk)
        self._advertised_rwnd -= len(chunk.user_data)
        for message in inbound_stream.pop_messages():
            self._advertised_rwnd += len(message[2])
            await self._receive(*message)

    async def _receive_sack_chunk(self, chunk):
        """
        Handle a SACK chunk.
        """
        if tsn_gt(self._last_sacked_tsn, chunk.cumulative_tsn):
            return

        received_time = time.time()
        self._last_sacked_tsn = chunk.cumulative_tsn
        done = 0
        done_bytes = 0
        restart_t3 = False

        # handle acknowledged data
        for i in range(len(self._outbound_queue)):
            schunk = self._outbound_queue[i]
            if tsn_gt(schunk.tsn, self._last_sacked_tsn):
                break
            done += 1
            if not schunk._acked:
                done_bytes += schunk._book_size
                self._flight_size_decrease(schunk)

            # update RTO estimate
            if done == 1 and schunk._sent_count == 1:
                self._update_rto(received_time - schunk._sent_time)

        # handle gap blocks
        loss = False
        if chunk.gaps:
            highest_seen_tsn = (chunk.cumulative_tsn + chunk.gaps[-1][1]) % SCTP_TSN_MODULO
            seen = set()
            for gap in chunk.gaps:
                for pos in range(gap[0], gap[1] + 1):
                    tsn = (chunk.cumulative_tsn + pos) % SCTP_TSN_MODULO
                    seen.add(tsn)
            for i in range(done, len(self._outbound_queue)):
                schunk = self._outbound_queue[i]
                if tsn_gt(schunk.tsn, highest_seen_tsn):
                    break
                if schunk.tsn not in seen:
                    schunk._misses += 1
                    if schunk._misses == 3:
                        schunk._misses = 0
                        schunk._retransmit = True

                        schunk._acked = False
                        self._flight_size_decrease(schunk)

                        loss = True
                        if i == done:
                            restart_t3 = True
                elif not schunk._acked:
                    done_bytes += schunk._book_size
                    schunk._acked = True
                    self._flight_size_decrease(schunk)

        # discard acknowledged data
        if done:
            self._outbound_queue = self._outbound_queue[done:]
            self._outbound_queue_pos = max(0, self._outbound_queue_pos - done)
            restart_t3 = True

        # adjust congestion window
        if self._fast_recovery_exit is None:
            if done:
                if self._cwnd <= self._ssthresh:
                    # slow start
                    self._cwnd += min(done_bytes, USERDATA_MAX_LENGTH)
                else:
                    # congestion avoidance
                    self._partial_bytes_acked += done_bytes
                    if self._partial_bytes_acked >= self._cwnd:
                        self._partial_bytes_acked -= self._cwnd
                        self._cwnd += USERDATA_MAX_LENGTH
            if loss:
                self._ssthresh = max(self._cwnd // 2, 4 * USERDATA_MAX_LENGTH)
                self._cwnd = self._ssthresh
                self._partial_bytes_acked = 0
                self._fast_recovery_exit = highest_seen_tsn
                self._fast_recovery_transmit = True
        elif tsn_gte(chunk.cumulative_tsn, self._fast_recovery_exit):
            self._fast_recovery_exit = None

        if not len(self._outbound_queue):
            # there is no outstanding data, stop T3
            self._t3_cancel()
        elif restart_t3:
            # the earliest outstanding chunk was acknowledged, restart T3
            self._t3_handle.cancel()
            self._t3_handle = None
            self._t3_start()

        await self._data_channel_flush()
        await self._transmit()

    async def _receive_reconfig_param(self, param):
        """
        Handle a RE-CONFIG parameter.
        """
        self.__log_debug('<< %s', param)

        if isinstance(param, StreamResetOutgoingParam):
            # mark closed inbound streams
            for stream_id in param.streams:
                self._inbound_streams.pop(stream_id, None)

                # close data channel
                channel = self._data_channels.get(stream_id)
                if channel:
                    self._data_channel_close(channel)

            # send response
            response_param = StreamResetResponseParam(
                response_sequence=param.request_sequence,
                result=1)
            self._reconfig_response_seq = param.request_sequence

            await self._send_reconfig_param(response_param)
        elif isinstance(param, StreamAddOutgoingParam):
            # increase inbound streams
            self._inbound_streams_count += param.new_streams

            # send response
            response_param = StreamResetResponseParam(
                response_sequence=param.request_sequence,
                result=1)
            self._reconfig_response_seq = param.request_sequence

            await self._send_reconfig_param(response_param)
        elif isinstance(param, StreamResetResponseParam):
            if (self._reconfig_request and
               param.response_sequence == self._reconfig_request.request_sequence):
                # mark closed streams
                for stream_id in self._reconfig_request.streams:
                    self._outbound_stream_seq.pop(stream_id, None)
                    self._data_channel_closed(stream_id)

                self._reconfig_request = None
                await self._transmit_reconfig()

    async def _send(self, stream_id, pp_id, user_data, ordered=True):
        """
        Send data ULP -> stream.
        """
        stream_seq = self._outbound_stream_seq.get(stream_id, 0)

        fragments = math.ceil(len(user_data) / USERDATA_MAX_LENGTH)
        pos = 0
        for fragment in range(0, fragments):
            chunk = DataChunk()
            chunk.flags = 0
            if not ordered:
                chunk.flags = SCTP_DATA_UNORDERED
            if fragment == 0:
                chunk.flags |= SCTP_DATA_FIRST_FRAG
            if fragment == fragments - 1:
                chunk.flags |= SCTP_DATA_LAST_FRAG
            chunk.tsn = self._local_tsn
            chunk.stream_id = stream_id
            chunk.stream_seq = stream_seq
            chunk.protocol = pp_id
            chunk.user_data = user_data[pos:pos + USERDATA_MAX_LENGTH]

            # initialize counters
            chunk._acked = False
            chunk._book_size = len(chunk.user_data)
            chunk._misses = 0
            chunk._retransmit = False
            chunk._sent_count = 0
            chunk._sent_time = None

            pos += USERDATA_MAX_LENGTH
            self._local_tsn = tsn_plus_one(self._local_tsn)
            self._outbound_queue.append(chunk)
        self._outbound_stream_seq[stream_id] = uint16_add(stream_seq, 1)

        # transmit outbound data
        if not self._t3_handle:
            await self._transmit()

    async def _send_chunk(self, chunk):
        """
        Transmit a chunk (no bundling for now).
        """
        self.__log_debug('> %s', chunk)
        packet = Packet(
            source_port=self._local_port,
            destination_port=self._remote_port,
            verification_tag=self._remote_verification_tag,
            chunks=[chunk])
        await self.transport._send_data(bytes(packet))

    async def _send_reconfig_param(self, param):
        chunk = ReconfigChunk()
        for k, cls in RECONFIG_PARAM_TYPES.items():
            if isinstance(param, cls):
                param_type = k
                break
        chunk.params.append((param_type, bytes(param)))

        self.__log_debug('>> %s', param)
        await self._send_chunk(chunk)

    async def _send_sack(self):
        """
        Build and send a selective acknowledgement (SACK) chunk.
        """
        gaps = []
        gap_next = None
        for tsn in sorted(self._sack_misordered):
            pos = (tsn - self._last_received_tsn) % SCTP_TSN_MODULO
            if tsn == gap_next:
                gaps[-1][1] = pos
            else:
                gaps.append([pos, pos])
            gap_next = tsn_plus_one(tsn)

        sack = SackChunk()
        sack.cumulative_tsn = self._last_received_tsn
        sack.advertised_rwnd = max(0, self._advertised_rwnd)
        sack.duplicates = self._sack_duplicates[:]
        sack.gaps = [tuple(x) for x in gaps]

        await self._send_chunk(sack)

        self._sack_duplicates.clear()
        self._sack_needed = False

    def _set_state(self, state):
        """
        Transition the SCTP association to a new state.
        """
        if state != self._association_state:
            self.__log_debug('- %s -> %s', self._association_state, state)
            self._association_state = state

        if state == self.State.ESTABLISHED:
            self.__state = 'connected'
            asyncio.ensure_future(self._data_channel_flush())
        elif state == self.State.CLOSED:
            self._t1_cancel()
            self._t2_cancel()
            self._t3_cancel()
            self.__state = 'closed'

            # close data channels
            for stream_id in list(self._data_channels.keys()):
                self._data_channel_closed(stream_id)

            # no more events will be emitted, so remove all event listeners
            # to facilitate garbage collection.
            self.remove_all_listeners()

    # timers

    def _t1_cancel(self):
        if self._t1_handle is not None:
            self.__log_debug('- T1(%s) cancel', chunk_type(self._t1_chunk))
            self._t1_handle.cancel()
            self._t1_handle = None
            self._t1_chunk = None

    def _t1_expired(self):
        self._t1_failures += 1
        self._t1_handle = None
        self.__log_debug('x T1(%s) expired %d', chunk_type(self._t1_chunk), self._t1_failures)
        if self._t1_failures > SCTP_MAX_INIT_RETRANS:
            self._set_state(self.State.CLOSED)
        else:
            asyncio.ensure_future(self._send_chunk(self._t1_chunk))
            self._t1_handle = self._loop.call_later(self._rto, self._t1_expired)

    def _t1_start(self, chunk):
        assert self._t1_handle is None
        self._t1_chunk = chunk
        self._t1_failures = 0
        self.__log_debug('- T1(%s) start', chunk_type(self._t1_chunk))
        self._t1_handle = self._loop.call_later(self._rto, self._t1_expired)

    def _t2_cancel(self):
        if self._t2_handle is not None:
            self.__log_debug('- T2(%s) cancel', chunk_type(self._t2_chunk))
            self._t2_handle.cancel()
            self._t2_handle = None
            self._t2_chunk = None

    def _t2_expired(self):
        self._t2_failures += 1
        self._t2_handle = None
        self.__log_debug('x T2(%s) expired %d', chunk_type(self._t2_chunk), self._t2_failures)
        if self._t2_failures > SCTP_MAX_ASSOCIATION_RETRANS:
            self._set_state(self.State.CLOSED)
        else:
            asyncio.ensure_future(self._send_chunk(self._t2_chunk))
            self._t2_handle = self._loop.call_later(self._rto, self._t2_expired)

    def _t2_start(self, chunk):
        assert self._t2_handle is None
        self._t2_chunk = chunk
        self._t2_failures = 0
        self.__log_debug('- T2(%s) start', chunk_type(self._t2_chunk))
        self._t2_handle = self._loop.call_later(self._rto, self._t2_expired)

    def _t3_expired(self):
        self._t3_handle = None
        self.__log_debug('x T3 expired')

        # retransmit
        self._flight_size = 0
        self._outbound_queue_pos = 0
        self._partial_bytes_acked = 0

        self._ssthresh = max(self._cwnd // 2, 4 * USERDATA_MAX_LENGTH)
        self._cwnd = USERDATA_MAX_LENGTH

        asyncio.ensure_future(self._transmit())

    def _t3_start(self):
        assert self._t3_handle is None
        self.__log_debug('- T3 start')
        self._t3_handle = self._loop.call_later(self._rto, self._t3_expired)

    def _t3_cancel(self):
        if self._t3_handle is not None:
            self.__log_debug('- T3 cancel')
            self._t3_handle.cancel()
            self._t3_handle = None

    async def _transmit(self):
        """
        Transmit outbound data.
        """
        # retransmit
        for pos in range(self._outbound_queue_pos):
            chunk = self._outbound_queue[pos]
            if chunk._retransmit:
                if self._fast_recovery_transmit:
                    self._fast_recovery_transmit = False
                elif self._flight_size + chunk._book_size > self._cwnd:
                    return
                self._flight_size_increase(chunk)

                chunk._retransmit = False
                chunk._sent_count += 1
                await self._send_chunk(chunk)

        while self._outbound_queue_pos < len(self._outbound_queue):
            chunk = self._outbound_queue[self._outbound_queue_pos]
            if self._flight_size + chunk._book_size > self._cwnd:
                break
            self._flight_size_increase(chunk)

            # update counters
            chunk._sent_count += 1
            chunk._sent_time = time.time()

            await self._send_chunk(chunk)
            if not self._t3_handle:
                self._t3_start()
            self._outbound_queue_pos += 1

    async def _transmit_reconfig(self):
        if self._reconfig_queue and not self._reconfig_request:
            streams = self._reconfig_queue[0:RECONFIG_MAX_STREAMS]
            self._reconfig_queue = self._reconfig_queue[RECONFIG_MAX_STREAMS:]
            param = StreamResetOutgoingParam(
                request_sequence=self._reconfig_request_seq,
                response_sequence=self._reconfig_response_seq,
                last_tsn=tsn_minus_one(self._local_tsn),
                streams=streams,
            )
            self._reconfig_request = param
            self._reconfig_request_seq = tsn_plus_one(self._reconfig_request_seq)

            await self._send_reconfig_param(param)

    def _update_rto(self, R):
        """
        Update RTO given a new roundtrip measurement R.
        """
        if self._srtt is None:
            self._rttvar = R / 2
            self._srtt = R
        else:
            self._rttvar = (1 - SCTP_RTO_BETA) * self._rttvar + SCTP_RTO_BETA * abs(self._srtt - R)
            self._srtt = (1 - SCTP_RTO_ALPHA) * self._srtt + SCTP_RTO_ALPHA * R
        self._rto = max(SCTP_RTO_MIN, min(self._srtt + 4 * self._rttvar, SCTP_RTO_MAX))

    def _data_channel_close(self, channel, transmit=True):
        """
        Request closing the datachannel by sending an Outgoing Stream Reset Request.
        """
        if channel.readyState not in ['closing', 'closed']:
            channel._setReadyState('closing')
            self._reconfig_queue.append(channel.id)
            if len(self._reconfig_queue) == 1:
                asyncio.ensure_future(self._transmit_reconfig())

    def _data_channel_closed(self, stream_id):
        channel = self._data_channels.pop(stream_id)
        channel._setReadyState('closed')

    async def _data_channel_flush(self):
        """
        Try to flush buffered data to the SCTP layer.

        We wait until the association is established, as we need to know
        whether we are a client or a server to correctly assign an odd/even ID
        to the data channels.
        """
        if self._association_state != self.State.ESTABLISHED:
            return

        while len(self._outbound_queue) < MAX_OUTBOUND_QUEUE and self._data_channel_queue:
            channel, protocol, user_data = self._data_channel_queue.pop(0)

            # register channel if necessary
            stream_id = channel.id
            if stream_id is None:
                stream_id = self._data_channel_id
                self._data_channels[stream_id] = channel
                self._data_channel_id += 2
                channel._setId(stream_id)

            # send data
            await self._send(stream_id, protocol, user_data, ordered=channel.ordered)
            if protocol in [WEBRTC_STRING_EMPTY, WEBRTC_STRING, WEBRTC_BINARY_EMPTY, WEBRTC_BINARY]:
                channel._addBufferedAmount(-len(user_data))

    def _data_channel_open(self, channel):
        if channel.ordered:
            channel_type = DATA_CHANNEL_RELIABLE
        else:
            channel_type = DATA_CHANNEL_RELIABLE_UNORDERED
        data = pack('!BBHLHH', DATA_CHANNEL_OPEN, channel_type,
                    0, 0, len(channel.label), len(channel.protocol))
        data += channel.label.encode('utf8')
        data += channel.protocol.encode('utf8')
        self._data_channel_queue.append((channel, WEBRTC_DCEP, data))
        asyncio.ensure_future(self._data_channel_flush())

    async def _data_channel_receive(self, stream_id, pp_id, data):
        if pp_id == WEBRTC_DCEP and len(data):
            msg_type = unpack('!B', data[0:1])[0]
            if msg_type == DATA_CHANNEL_OPEN and len(data) >= 12:
                # we should not receive an open for an existing channel
                assert stream_id not in self._data_channels

                # one side should be using even IDs, the other odd IDs
                assert (stream_id % 2) != (self._data_channel_id % 2)

                (msg_type, channel_type, priority, reliability,
                 label_length, protocol_length) = unpack('!BBHLHH', data[0:12])
                pos = 12
                label = data[pos:pos + label_length].decode('utf8')
                pos += label_length
                protocol = data[pos:pos + protocol_length].decode('utf8')

                # check channel type is supported
                assert channel_type in [DATA_CHANNEL_RELIABLE, DATA_CHANNEL_RELIABLE_UNORDERED]

                # register channel
                parameters = RTCDataChannelParameters(
                    label=label,
                    ordered=(channel_type & 0x80) == 0,
                    protocol=protocol)
                channel = RTCDataChannel(self, parameters, id=stream_id)
                channel._setReadyState('open')
                self._data_channels[stream_id] = channel

                # send ack
                self._data_channel_queue.append(
                    (channel, WEBRTC_DCEP, pack('!B', DATA_CHANNEL_ACK)))
                await self._data_channel_flush()

                # emit channel
                self.emit('datachannel', channel)
            elif msg_type == DATA_CHANNEL_ACK:
                assert stream_id in self._data_channels
                channel = self._data_channels[stream_id]
                channel._setReadyState('open')
        elif pp_id == WEBRTC_STRING and stream_id in self._data_channels:
            # emit message
            self._data_channels[stream_id].emit('message', data.decode('utf8'))
        elif pp_id == WEBRTC_STRING_EMPTY and stream_id in self._data_channels:
            # emit message
            self._data_channels[stream_id].emit('message', '')
        elif pp_id == WEBRTC_BINARY and stream_id in self._data_channels:
            # emit message
            self._data_channels[stream_id].emit('message', data)
        elif pp_id == WEBRTC_BINARY_EMPTY and stream_id in self._data_channels:
            # emit message
            self._data_channels[stream_id].emit('message', b'')

    def _data_channel_send(self, channel, data):
        if data == '':
            pp_id, user_data = WEBRTC_STRING_EMPTY, b'\x00'
        elif isinstance(data, str):
            pp_id, user_data = WEBRTC_STRING, data.encode('utf8')
        elif data == b'':
            pp_id, user_data = WEBRTC_BINARY_EMPTY, b'\x00'
        else:
            pp_id, user_data = WEBRTC_BINARY, data

        channel._addBufferedAmount(len(user_data))
        self._data_channel_queue.append((channel, pp_id, user_data))
        asyncio.ensure_future(self._data_channel_flush())

    def __log_debug(self, msg, *args):
        role = self.is_server and 'server' or 'client'
        logger.debug(role + ' ' + msg, *args)

    class State(enum.Enum):
        CLOSED = 1
        COOKIE_WAIT = 2
        COOKIE_ECHOED = 3
        ESTABLISHED = 4
        SHUTDOWN_PENDING = 5
        SHUTDOWN_SENT = 6
        SHUTDOWN_RECEIVED = 7
        SHUTDOWN_ACK_SENT = 8
