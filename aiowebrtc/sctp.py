import asyncio
import enum
import logging
import os
from struct import pack, unpack

import crcmod.predefined


crc32c = crcmod.predefined.mkPredefinedCrcFun('crc-32c')
logger = logging.getLogger('sctp')

SCTP_DATA_LAST_FRAG = 0x01
SCTP_DATA_FIRST_FRAG = 0x02

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


def randl():
    return unpack('!L', os.urandom(4))[0]


def swapl(i):
    return unpack("<I", pack(">I", i))[0]


class Error(Exception):
    pass


class Chunk:
    def __bytes__(self):
        body = self.body
        data = pack('!BBH', self.type, self.flags, len(body) + 4) + body
        data += b'\x00' * padl(len(body))
        return data


class ChunkType(enum.IntEnum):
    DATA = 0
    INIT = 1
    INIT_ACK = 2
    SACK = 3
    HEARTBEAT = 4
    HEARTBEAT_ACK = 5
    ABORT = 6
    SHUTDOWN = 7
    SHUTDOWN_ACK = 8
    ERROR = 9
    COOKIE_ECHO = 10
    COOKIE_ACK = 11
    SHUTDOWN_COMPLETE = 14


class AbortChunk(Chunk):
    type = ChunkType.ABORT

    def __init__(self, flags=0, body=None):
        self.flags = flags
        if body:
            self.params = decode_params(body)
        else:
            self.params = []

    @property
    def body(self):
        return encode_params(self.params)


class CookieAckChunk(Chunk):
    type = ChunkType.COOKIE_ACK

    def __init__(self, flags=0, body=None):
        self.flags = flags
        self.body = b''


class CookieEchoChunk(Chunk):
    type = ChunkType.COOKIE_ECHO

    def __init__(self, flags=0, body=b''):
        self.flags = flags
        self.body = body


class DataChunk(Chunk):
    type = ChunkType.DATA

    def __init__(self, flags=0, body=None):
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


class InitChunk(Chunk):
    type = ChunkType.INIT

    def __init__(self, flags=0, body=None):
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


class InitAckChunk(InitChunk):
    type = ChunkType.INIT_ACK


class SackChunk(Chunk):
    type = ChunkType.SACK

    def __init__(self, flags=0, body=None):
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
    type = ChunkType.SHUTDOWN

    def __init__(self, flags=0, body=None):
        self.flags = flags
        if body:
            self.cumulative_tsn = unpack('!L', body[0:4])[0]
        else:
            self.cumulative_tsn = 0

    @property
    def body(self):
        return pack('!L', self.cumulative_tsn)


class ShutdownAckChunk(Chunk):
    type = ChunkType.SHUTDOWN_ACK

    def __init__(self, flags=0, body=b''):
        self.flags = flags
        self.body = body


class ShutdownCompleteChunk(Chunk):
    type = ChunkType.SHUTDOWN_COMPLETE

    def __init__(self, flags=0, body=b''):
        self.flags = flags
        self.body = body


class UnknownChunk(Chunk):
    def __init__(self, type, flags, body):
        self.type = type
        self.flags = flags
        self.body = body


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
            if chunk_type == ChunkType.DATA:
                cls = DataChunk
            elif chunk_type == ChunkType.INIT:
                cls = InitChunk
            elif chunk_type == ChunkType.INIT_ACK:
                cls = InitAckChunk
            elif chunk_type == ChunkType.SACK:
                cls = SackChunk
            elif chunk_type == ChunkType.ABORT:
                cls = AbortChunk
            elif chunk_type == ChunkType.SHUTDOWN:
                cls = ShutdownChunk
            elif chunk_type == ChunkType.SHUTDOWN_ACK:
                cls = ShutdownAckChunk
            elif chunk_type == ChunkType.SHUTDOWN_COMPLETE:
                cls = ShutdownCompleteChunk
            elif chunk_type == ChunkType.COOKIE_ECHO:
                cls = CookieEchoChunk
            elif chunk_type == ChunkType.COOKIE_ACK:
                cls = CookieAckChunk
            else:
                cls = None

            if cls:
                packet.chunks.append(cls(
                    flags=chunk_flags,
                    body=chunk_body))
            else:
                packet.chunks.append(UnknownChunk(
                    type=chunk_type,
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

        self.local_initiate_tag = randl()
        self.advertised_rwnd = 131072
        self.outbound_streams = 256
        self.inbound_streams = 2048
        self.stream_seq = 0
        self.local_tsn = randl()

        self.remote_initiate_tag = 0

    async def close(self):
        if self.state == self.State.CLOSED:
            self.closed.set()
            return

        chunk = ShutdownChunk()
        await self.__send_chunk(chunk)
        self.set_state(self.State.SHUTDOWN_SENT)
        await self.closed.wait()

    async def recv(self):
        done, pending = await asyncio.wait([self.recv_queue.get(), self.closed.wait()],
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        result = done.pop().result()
        if result is True:
            raise Error('Connection closed while receiving data')
        return result

    async def send(self, stream_id, protocol, user_data):
        self.send_queue.append((stream_id, protocol, user_data))
        await self.__flush()

    async def run(self):
        if not self.is_server:
            chunk = InitChunk()
            chunk.initiate_tag = self.local_initiate_tag
            chunk.advertised_rwnd = self.advertised_rwnd
            chunk.outbound_streams = self.outbound_streams
            chunk.inbound_streams = self.inbound_streams
            chunk.initial_tsn = self.local_tsn
            await self.__send_chunk(chunk)
            self.set_state(self.State.COOKIE_WAIT)

        while True:
            done, pending = await asyncio.wait(
                [self.transport.recv(), self.closed.wait()],
                return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            data = done.pop().result()
            if data is True:
                break

            try:
                packet = Packet.parse(data)
            except ValueError:
                continue

            for chunk in packet.chunks:
                await self.__receive_chunk(chunk)

    async def __flush(self):
        if self.state != self.State.ESTABLISHED:
            return

        for stream_id, protocol, user_data in self.send_queue:
            # FIXME : handle fragmentation!
            chunk = DataChunk()
            chunk.flags = SCTP_DATA_FIRST_FRAG | SCTP_DATA_LAST_FRAG
            chunk.tsn = self.local_tsn
            chunk.stream_id = stream_id
            chunk.stream_seq = self.stream_seq
            chunk.protocol = protocol
            chunk.user_data = user_data

            self.local_tsn += 1
            self.stream_seq += 1
            await self.__send_chunk(chunk)
        self.send_queue = []

    async def __receive_chunk(self, chunk):
        logger.debug('%s < %s', self.role, chunk.__class__.__name__)
        if isinstance(chunk, InitChunk) and self.is_server:
            self.remote_initiate_tag = chunk.initiate_tag

            ack = InitAckChunk()
            ack.initiate_tag = self.local_initiate_tag
            ack.advertised_rwnd = self.advertised_rwnd
            ack.outbound_streams = self.outbound_streams
            ack.inbound_streams = self.inbound_streams
            ack.initial_tsn = self.local_tsn
            ack.params.append((STATE_COOKIE, b'12345678'))
            await self.__send_chunk(ack)
        elif isinstance(chunk, InitAckChunk) and not self.is_server:
            echo = CookieEchoChunk()
            await self.__send_chunk(echo)
            self.set_state(self.State.COOKIE_ECHOED)
        elif isinstance(chunk, CookieEchoChunk) and self.is_server:
            ack = CookieAckChunk()
            await self.__send_chunk(ack)
            self.set_state(self.State.ESTABLISHED)
        elif isinstance(chunk, CookieAckChunk) and not self.is_server:
            self.set_state(self.State.ESTABLISHED)
        elif isinstance(chunk, DataChunk):
            sack = SackChunk()
            sack.cumulative_tsn = chunk.tsn
            await self.__send_chunk(sack)
            await self.recv_queue.put((chunk.stream_id, chunk.protocol, chunk.user_data))
        elif isinstance(chunk, AbortChunk):
            self.set_state(self.State.CLOSED)
        elif isinstance(chunk, ShutdownChunk):
            self.set_state(self.State.SHUTDOWN_RECEIVED)
            ack = ShutdownAckChunk()
            await self.__send_chunk(ack)
            self.set_state(self.State.SHUTDOWN_ACK_SENT)
        elif isinstance(chunk, ShutdownAckChunk):
            complete = ShutdownCompleteChunk()
            await self.__send_chunk(complete)
            self.set_state(self.State.CLOSED)
        elif isinstance(chunk, ShutdownCompleteChunk):
            self.set_state(self.State.CLOSED)

    async def __send_chunk(self, chunk):
        logger.debug('%s > %s', self.role, chunk.__class__.__name__)
        packet = Packet(
            source_port=5000,
            destination_port=5000,
            verification_tag=self.remote_initiate_tag)
        packet.chunks.append(chunk)
        await self.transport.send(bytes(packet))

    def set_state(self, state):
        if state != self.state:
            logger.debug('%s - %s -> %s' % (self.role, self.state, state))
            self.state = state
            if state == self.State.ESTABLISHED:
                asyncio.ensure_future(self.__flush())
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
