import asyncio
import enum
import hmac
import logging
import math
import os
import time
from struct import pack, unpack

import crcmod.predefined

from .utils import first_completed, random32

crc32c = crcmod.predefined.mkPredefinedCrcFun('crc-32c')
logger = logging.getLogger('sctp')

# local constants
COOKIE_LENGTH = 24
COOKIE_LIFETIME = 60
USERDATA_MAX_LENGTH = 1200

# protocol constants
SCTP_DATA_LAST_FRAG = 0x01
SCTP_DATA_FIRST_FRAG = 0x02
SCTP_SEQ_MODULO = 2 ** 16
SCTP_TSN_MODULO = 2 ** 32

STALE_COOKIE_ERROR = 3

STATE_COOKIE = 0x0007


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
        return '%s(flags=%d)' % (self.__class__.__name__, self.flags)

    @property
    def type(self):
        for k, cls in CHUNK_TYPES.items():
            if isinstance(self, cls):
                return k


class AbortChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        if body:
            self.params = decode_params(body)
        else:
            self.params = []

    @property
    def body(self):
        return encode_params(self.params)


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


class ErrorChunk(Chunk):
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


class SackChunk(Chunk):
    def __init__(self, flags=0, body=b''):
        self.flags = flags
        self.gaps = []
        self.duplicates = []
        if body:
            self.cumulative_tsn, self.advertised_rwnd, nb_gaps, nb_duplicates = unpack(
                '!LLHH', body[0:12])
        else:
            self.cumulative_tsn = 0
            self.advertised_rwnd = 0

    @property
    def body(self):
        body = pack('!LLHH', self.cumulative_tsn, self.advertised_rwnd,
                    len(self.gaps), len(self.duplicates))
        return body


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


class ShutdownAckChunk(Chunk):
    pass


class ShutdownCompleteChunk(Chunk):
    pass


CHUNK_TYPES = {
    0: DataChunk,
    1: InitChunk,
    2: InitAckChunk,
    3: SackChunk,
    6: AbortChunk,
    7: ShutdownChunk,
    8: ShutdownAckChunk,
    9: ErrorChunk,
    10: CookieEchoChunk,
    11: CookieAckChunk,
    14: ShutdownCompleteChunk,
}


class Packet:
    def __init__(self, source_port, destination_port, verification_tag):
        self.source_port = source_port
        self.destination_port = destination_port
        self.verification_tag = verification_tag
        self.chunks = []

    def __bytes__(self):
        checksum = 0
        data = pack(
            '!HHII',
            self.source_port,
            self.destination_port,
            self.verification_tag,
            checksum)
        for chunk in self.chunks:
            data += bytes(chunk)

        # calculate checksum
        checksum = swapl(crc32c(data))
        return data[0:8] + pack('!I', checksum) + data[12:]

    @classmethod
    def parse(cls, data):
        if len(data) < 12:
            raise ValueError('SCTP packet length is less than 12 bytes')

        source_port, destination_port, verification_tag, checksum = unpack(
            '!HHII', data[0:12])

        # verify checksum
        check_data = data[0:8] + b'\x00\x00\x00\x00' + data[12:]
        if checksum != swapl(crc32c(check_data)):
            raise ValueError('SCTP packet has invalid checksum')

        packet = cls(
            source_port=source_port,
            destination_port=destination_port,
            verification_tag=verification_tag)

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


class Endpoint:
    def __init__(self, is_server, transport):
        self.is_server = is_server
        self.recv_queue = asyncio.Queue()
        self.send_queue = []
        self.role = is_server and 'server' or 'client'
        self.state = self.State.CLOSED
        self.transport = transport
        self.closed = asyncio.Event()

        self.hmac_key = os.urandom(16)
        self.advertised_rwnd = 131072
        self.outbound_streams = 256
        self.inbound_streams = 2048
        self.stream_frags = {}
        self.stream_seq = {}

        self.local_tsn = random32()
        self.local_verification_tag = random32()

        self.remote_tsn = None
        self.remote_verification_tag = 0

    async def abort(self):
        chunk = AbortChunk()
        await self._send_chunk(chunk)
        self._set_state(self.State.CLOSED)

    async def close(self):
        if self.state == self.State.CLOSED:
            self.closed.set()
            return

        chunk = ShutdownChunk()
        await self._send_chunk(chunk)
        self._set_state(self.State.SHUTDOWN_SENT)
        await self.closed.wait()

    async def recv(self):
        data = await first_completed(self.recv_queue.get(), self.closed.wait())
        if data is True:
            raise ConnectionError
        return data

    async def send(self, stream_id, protocol, user_data):
        self.send_queue.append((stream_id, protocol, user_data))
        await self._flush()

    async def run(self):
        if not self.is_server:
            chunk = InitChunk()
            chunk.initiate_tag = self.local_verification_tag
            chunk.advertised_rwnd = self.advertised_rwnd
            chunk.outbound_streams = self.outbound_streams
            chunk.inbound_streams = self.inbound_streams
            chunk.initial_tsn = self.local_tsn
            await self._send_chunk(chunk)
            self._set_state(self.State.COOKIE_WAIT)

        while True:
            data = await first_completed(self.transport.recv(), self.closed.wait())
            if data is True:
                break

            try:
                packet = Packet.parse(data)
            except ValueError:
                continue

            # is this an init?
            init_chunk = len([x for x in packet.chunks if isinstance(x, InitChunk)])
            if init_chunk:
                assert len(packet.chunks) == 1
                expected_tag = 0
            else:
                expected_tag = self.local_verification_tag

            # verify tag
            if packet.verification_tag != expected_tag:
                logger.warning('%s x Bad verification tag %d vs %d' % (
                    self.role, packet.verification_tag, expected_tag))
                return

            for chunk in packet.chunks:
                await self._receive_chunk(chunk)

    async def _flush(self):
        if self.state != self.State.ESTABLISHED:
            return

        for stream_id, protocol, user_data in self.send_queue:
            stream_seq = self.stream_seq.get(stream_id, 0)

            fragments = math.ceil(len(user_data) / USERDATA_MAX_LENGTH)
            pos = 0
            for fragment in range(0, fragments):
                chunk = DataChunk()
                chunk.flags = 0
                if fragment == 0:
                    chunk.flags |= SCTP_DATA_FIRST_FRAG
                if fragment == fragments - 1:
                    chunk.flags |= SCTP_DATA_LAST_FRAG
                chunk.tsn = self.local_tsn
                chunk.stream_id = stream_id
                chunk.stream_seq = stream_seq
                chunk.protocol = protocol
                chunk.user_data = user_data[pos:pos + USERDATA_MAX_LENGTH]

                pos += USERDATA_MAX_LENGTH
                self.local_tsn = (self.local_tsn + 1) % SCTP_TSN_MODULO
                await self._send_chunk(chunk)

            self.stream_seq[stream_id] = (stream_seq + 1) % SCTP_SEQ_MODULO

        self.send_queue = []

    def _get_timestamp(self):
        return int(time.time())

    async def _receive_chunk(self, chunk):
        logger.debug('%s < %s', self.role, repr(chunk))

        # server
        if isinstance(chunk, InitChunk) and self.is_server:
            self.remote_tsn = chunk.initial_tsn
            self.remote_verification_tag = chunk.initiate_tag

            ack = InitAckChunk()
            ack.initiate_tag = self.local_verification_tag
            ack.advertised_rwnd = self.advertised_rwnd
            ack.outbound_streams = self.outbound_streams
            ack.inbound_streams = self.inbound_streams
            ack.initial_tsn = self.local_tsn

            # generate state cookie
            cookie = pack('!L', self._get_timestamp())
            cookie += hmac.new(self.hmac_key, cookie, 'sha1').digest()
            ack.params.append((STATE_COOKIE, cookie))
            await self._send_chunk(ack)
        elif isinstance(chunk, CookieEchoChunk) and self.is_server:
            # check state cookie MAC
            cookie = chunk.body
            if (len(cookie) != COOKIE_LENGTH or
               hmac.new(self.hmac_key, cookie[0:4], 'sha1').digest() != cookie[4:]):
                return

            # check state cookie lifetime
            now = self._get_timestamp()
            stamp = unpack('!L', cookie[0:4])[0]
            if stamp < now - COOKIE_LIFETIME or stamp > now:
                logger.warning('State cookie has expired')
                error = ErrorChunk()
                error.body = pack('!HHL', STALE_COOKIE_ERROR, 8, 0)
                await self._send_chunk(error)
                return

            ack = CookieAckChunk()
            await self._send_chunk(ack)
            self._set_state(self.State.ESTABLISHED)

        # client
        if isinstance(chunk, InitAckChunk) and not self.is_server:
            self.remote_tsn = chunk.initial_tsn
            self.remote_verification_tag = chunk.initiate_tag

            echo = CookieEchoChunk()
            for k, v in chunk.params:
                if k == STATE_COOKIE:
                    echo.body = v
                    break
            await self._send_chunk(echo)
            self._set_state(self.State.COOKIE_ECHOED)
        elif isinstance(chunk, CookieAckChunk) and not self.is_server:
            self._set_state(self.State.ESTABLISHED)
        elif (isinstance(chunk, ErrorChunk) and not self.is_server and
              self.state in [self.State.COOKIE_WAIT, self.State.COOKIE_ECHOED]):
            self._set_state(self.State.CLOSED)
            logger.warning('Could not establish association')
            return

        # common
        elif isinstance(chunk, DataChunk):
            sack = SackChunk()
            sack.cumulative_tsn = chunk.tsn
            await self._send_chunk(sack)

            # defragment data
            if chunk.flags & SCTP_DATA_FIRST_FRAG:
                self.stream_frags[chunk.stream_id] = chunk.user_data
            else:
                self.stream_frags[chunk.stream_id] += chunk.user_data
            if chunk.flags & SCTP_DATA_LAST_FRAG:
                user_data = self.stream_frags.pop(chunk.stream_id)
                await self.recv_queue.put((chunk.stream_id, chunk.protocol, user_data))
        elif isinstance(chunk, AbortChunk):
            logger.warning('Association was aborted by remote party')
            self._set_state(self.State.CLOSED)
            return
        elif isinstance(chunk, ShutdownChunk):
            self._set_state(self.State.SHUTDOWN_RECEIVED)
            ack = ShutdownAckChunk()
            await self._send_chunk(ack)
            self._set_state(self.State.SHUTDOWN_ACK_SENT)
        elif isinstance(chunk, ShutdownAckChunk):
            complete = ShutdownCompleteChunk()
            await self._send_chunk(complete)
            self._set_state(self.State.CLOSED)
        elif isinstance(chunk, ShutdownCompleteChunk):
            self._set_state(self.State.CLOSED)

    async def _send_chunk(self, chunk):
        logger.debug('%s > %s', self.role, repr(chunk))
        packet = Packet(
            source_port=5000,
            destination_port=5000,
            verification_tag=self.remote_verification_tag)
        packet.chunks.append(chunk)
        await self.transport.send(bytes(packet))

    def _set_state(self, state):
        if state != self.state:
            logger.debug('%s - %s -> %s' % (self.role, self.state, state))
            self.state = state
            if state == self.State.ESTABLISHED:
                asyncio.ensure_future(self._flush())
            elif state == self.State.CLOSED:
                self.closed.set()

    class State(enum.Enum):
        CLOSED = 1
        COOKIE_WAIT = 2
        COOKIE_ECHOED = 3
        ESTABLISHED = 4
        SHUTDOWN_PENDING = 5
        SHUTDOWN_SENT = 6
        SHUTDOWN_RECEIVED = 7
        SHUTDOWN_ACK_SENT = 8
