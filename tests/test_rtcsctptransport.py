import asyncio
import contextlib
from collections.abc import AsyncGenerator, Generator
from typing import TypeVar, cast
from unittest import TestCase
from unittest.mock import MagicMock, patch

from aiortc.exceptions import InvalidStateError
from aiortc.rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from aiortc.rtcsctptransport import (
    SCTP_DATA_FIRST_FRAG,
    SCTP_DATA_LAST_FRAG,
    SCTP_DATA_UNORDERED,
    USERDATA_MAX_LENGTH,
    AbortChunk,
    Chunk,
    CookieEchoChunk,
    DataChunk,
    ErrorChunk,
    ForwardTsnChunk,
    HeartbeatAckChunk,
    HeartbeatChunk,
    InboundStream,
    InitChunk,
    ReconfigChunk,
    RTCSctpCapabilities,
    RTCSctpTransport,
    SackChunk,
    ShutdownAckChunk,
    ShutdownChunk,
    ShutdownCompleteChunk,
    StreamAddOutgoingParam,
    StreamResetOutgoingParam,
    StreamResetResponseParam,
    parse_packet,
    serialize_packet,
    tsn_minus_one,
    tsn_plus_one,
)

from .utils import (
    ClosedDtlsTransport,
    asynctest,
    dummy_dtls_transport_pair,
    load,
    set_loss_pattern,
)

T = TypeVar("T")


@contextlib.asynccontextmanager
async def client_and_server() -> AsyncGenerator[
    tuple[RTCSctpTransport, RTCSctpTransport], None
]:
    async with dummy_dtls_transport_pair() as (client_transport, server_transport):
        client = RTCSctpTransport(client_transport)
        server = RTCSctpTransport(server_transport)
        assert client.is_server is False
        assert server.is_server is True

        try:
            yield client, server
        finally:
            await client.stop()
            await server.stop()
            assert client._association_state == RTCSctpTransport.State.CLOSED
            assert client.state == "closed"
            assert server._association_state == RTCSctpTransport.State.CLOSED
            assert server.state == "closed"


@contextlib.asynccontextmanager
async def client_standalone() -> AsyncGenerator[RTCSctpTransport, None]:
    async with dummy_dtls_transport_pair() as (client_transport, _):
        client = RTCSctpTransport(client_transport)
        assert client.is_server is False

        try:
            yield client
        finally:
            await client.stop()


async def noop_send_chunk(chunk: Chunk) -> None:
    pass


def outstanding_tsns(client: RTCSctpTransport) -> list[int]:
    return [chunk.tsn for chunk in client._sent_queue]


def queued_tsns(client: RTCSctpTransport) -> list[int]:
    return [chunk.tsn for chunk in client._outbound_queue]


def track_channels(transport: RTCSctpTransport) -> list[RTCDataChannel]:
    channels = []

    @transport.on("datachannel")
    def on_datachannel(channel: RTCDataChannel) -> None:
        channels.append(channel)

    return channels


async def wait_for_outcome(client: RTCSctpTransport, server: RTCSctpTransport) -> None:
    final = [RTCSctpTransport.State.ESTABLISHED, RTCSctpTransport.State.CLOSED]
    for i in range(100):
        if client._association_state in final and server._association_state in final:
            break
        await asyncio.sleep(0.1)


class SctpPacketTest(TestCase):
    def roundtrip_packet(self, data: bytes, cls: type[T]) -> T:
        source_port, destination_port, verification_tag, chunks = parse_packet(data)
        self.assertEqual(source_port, 5000)
        self.assertEqual(destination_port, 5000)
        self.assertEqual(len(chunks), 1)
        output = serialize_packet(
            source_port, destination_port, verification_tag, chunks[0]
        )
        self.assertEqual(output, data)

        self.assertIsInstance(chunks[0], cls)
        return cast(T, chunks[0])

    def test_parse_init(self) -> None:
        data = load("sctp_init.bin")
        chunk = self.roundtrip_packet(data, InitChunk)

        self.assertEqual(chunk.type, 1)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(len(chunk.body), 82)
        self.assertEqual(repr(chunk), "InitChunk(flags=0)")

    def test_parse_init_invalid_checksum(self) -> None:
        data = load("sctp_init.bin")
        data = data[0:8] + b"\x01\x02\x03\x04" + data[12:]
        with self.assertRaises(ValueError) as cm:
            self.roundtrip_packet(data, None)
        self.assertEqual(str(cm.exception), "SCTP packet has invalid checksum")

    def test_parse_init_truncated_packet_header(self) -> None:
        data = load("sctp_init.bin")[0:10]
        with self.assertRaises(ValueError) as cm:
            self.roundtrip_packet(data, None)
        self.assertEqual(str(cm.exception), "SCTP packet length is less than 12 bytes")

    def test_parse_cookie_echo(self) -> None:
        data = load("sctp_cookie_echo.bin")
        chunk = self.roundtrip_packet(data, CookieEchoChunk)

        self.assertEqual(chunk.type, 10)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(len(chunk.body), 8)

    def test_parse_abort(self) -> None:
        data = load("sctp_abort.bin")
        chunk = self.roundtrip_packet(data, AbortChunk)

        self.assertEqual(chunk.type, 6)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(
            chunk.params, [(13, b"Expected B-bit for TSN=4ce1f17f, SID=0001, SSN=0000")]
        )

    def test_parse_data(self) -> None:
        data = load("sctp_data.bin")
        chunk = self.roundtrip_packet(data, DataChunk)

        self.assertEqual(chunk.type, 0)
        self.assertEqual(chunk.flags, 3)
        self.assertEqual(chunk.tsn, 2584679421)
        self.assertEqual(chunk.stream_id, 1)
        self.assertEqual(chunk.stream_seq, 1)
        self.assertEqual(chunk.protocol, 51)
        self.assertEqual(chunk.user_data, b"ping")
        self.assertEqual(
            repr(chunk), "DataChunk(flags=3, tsn=2584679421, stream_id=1, stream_seq=1)"
        )

    def test_parse_data_padding(self) -> None:
        data = load("sctp_data_padding.bin")
        chunk = self.roundtrip_packet(data, DataChunk)

        self.assertEqual(chunk.type, 0)
        self.assertEqual(chunk.flags, 3)
        self.assertEqual(chunk.tsn, 2584679421)
        self.assertEqual(chunk.stream_id, 1)
        self.assertEqual(chunk.stream_seq, 1)
        self.assertEqual(chunk.protocol, 51)
        self.assertEqual(chunk.user_data, b"M")
        self.assertEqual(
            repr(chunk), "DataChunk(flags=3, tsn=2584679421, stream_id=1, stream_seq=1)"
        )

    def test_parse_error(self) -> None:
        data = load("sctp_error.bin")
        chunk = self.roundtrip_packet(data, ErrorChunk)

        self.assertEqual(chunk.type, 9)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(chunk.params, [(1, b"\x30\x39\x00\x00")])

    def test_parse_forward_tsn(self) -> None:
        data = load("sctp_forward_tsn.bin")
        chunk = self.roundtrip_packet(data, ForwardTsnChunk)

        self.assertEqual(chunk.type, 192)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(chunk.cumulative_tsn, 1234)
        self.assertEqual(chunk.streams, [(12, 34)])
        self.assertEqual(
            repr(chunk), "ForwardTsnChunk(cumulative_tsn=1234, streams=[(12, 34)])"
        )

    def test_parse_heartbeat(self) -> None:
        data = load("sctp_heartbeat.bin")
        chunk = self.roundtrip_packet(data, HeartbeatChunk)

        self.assertEqual(chunk.type, 4)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(
            chunk.params,
            [
                (
                    1,
                    b"\xb5o\xaaZvZ\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00{\x10\x00\x00"
                    b"\x004\xeb\x07F\x10\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                )
            ],
        )

    def test_parse_reconfig_reset_out(self) -> None:
        data = load("sctp_reconfig_reset_out.bin")
        chunk = self.roundtrip_packet(data, ReconfigChunk)

        self.assertEqual(chunk.type, 130)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(
            chunk.params, [(13, b"\x8b\xd8\n[\xe4\x8b\xecs\x8b\xd8\n^\x00\x01")]
        )

        # Outgoing SSN Reset Request Parameter
        param_data = chunk.params[0][1]
        param = StreamResetOutgoingParam.parse(param_data)
        self.assertEqual(param.request_sequence, 2346191451)
        self.assertEqual(param.response_sequence, 3834375283)
        self.assertEqual(param.last_tsn, 2346191454)
        self.assertEqual(param.streams, [1])
        self.assertEqual(bytes(param), param_data)

    def test_parse_reconfig_add_out(self) -> None:
        data = load("sctp_reconfig_add_out.bin")
        chunk = self.roundtrip_packet(data, ReconfigChunk)

        self.assertEqual(chunk.type, 130)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(chunk.params, [(17, b"\xca\x02\xf60\x00\x10\x00\x00")])

        # Add Outgoing Streams Request Parameter
        param_data = chunk.params[0][1]
        param = StreamAddOutgoingParam.parse(param_data)
        self.assertEqual(param.request_sequence, 3389191728)
        self.assertEqual(param.new_streams, 16)
        self.assertEqual(bytes(param), param_data)

    def test_parse_reconfig_response(self) -> None:
        data = load("sctp_reconfig_response.bin")
        chunk = self.roundtrip_packet(data, ReconfigChunk)

        self.assertEqual(chunk.type, 130)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(chunk.params, [(16, b"\x91S\x1fT\x00\x00\x00\x01")])

        # Re-configuration Response Parameter
        param_data = chunk.params[0][1]
        param = StreamResetResponseParam.parse(param_data)
        self.assertEqual(param.response_sequence, 2438143828)
        self.assertEqual(param.result, 1)
        self.assertEqual(bytes(param), param_data)

    def test_parse_sack(self) -> None:
        data = load("sctp_sack.bin")
        chunk = self.roundtrip_packet(data, SackChunk)

        self.assertEqual(chunk.type, 3)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(chunk.cumulative_tsn, 2222939037)
        self.assertEqual(chunk.gaps, [(2, 2), (4, 4)])
        self.assertEqual(chunk.duplicates, [2222939041])
        self.assertEqual(
            repr(chunk),
            "SackChunk(flags=0, advertised_rwnd=128160, cumulative_tsn=2222939037, "
            "gaps=[(2, 2), (4, 4)])",
        )

    def test_parse_shutdown(self) -> None:
        data = load("sctp_shutdown.bin")
        chunk = self.roundtrip_packet(data, ShutdownChunk)

        self.assertEqual(
            repr(chunk), "ShutdownChunk(flags=0, cumulative_tsn=2696426712)"
        )
        self.assertEqual(chunk.type, 7)
        self.assertEqual(chunk.flags, 0)
        self.assertEqual(chunk.cumulative_tsn, 2696426712)


class ChunkFactory:
    def __init__(self, tsn: int = 1) -> None:
        self.tsn = tsn
        self.stream_seq = 0

    def create(self, frags: list[bytes], ordered: bool = True) -> list[DataChunk]:
        chunks = []

        for i, frag in enumerate(frags):
            flags = 0
            if not ordered:
                flags |= SCTP_DATA_UNORDERED
            if i == 0:
                flags |= SCTP_DATA_FIRST_FRAG
            if i == len(frags) - 1:
                flags |= SCTP_DATA_LAST_FRAG

            chunk = DataChunk(flags=flags)
            chunk.protocol = 123
            chunk.stream_id = 456
            if ordered:
                chunk.stream_seq = self.stream_seq
            chunk.tsn = self.tsn
            chunk.user_data = frag
            chunks.append(chunk)

            self.tsn += 1

        if ordered:
            self.stream_seq += 1

        return chunks


class SctpStreamTest(TestCase):
    def setUp(self) -> None:
        self.factory = ChunkFactory()

    def test_duplicate(self) -> None:
        stream = InboundStream()
        chunks = self.factory.create([b"foo", b"bar", b"baz"])

        # feed first chunk
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[0]])
        self.assertEqual(stream.sequence_number, 0)

        # feed first chunk again
        with self.assertRaises(AssertionError) as cm:
            stream.add_chunk(chunks[0])
        self.assertEqual(str(cm.exception), "duplicate chunk in reassembly")

    def test_whole_in_order(self) -> None:
        stream = InboundStream()
        chunks = self.factory.create([b"foo"]) + self.factory.create([b"bar"])

        # feed first unfragmented
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"foo")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 1)

        # feed second unfragmented
        stream.add_chunk(chunks[1])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 1)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"bar")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 2)

    def test_whole_out_of_order(self) -> None:
        stream = InboundStream()
        chunks = (
            self.factory.create([b"foo"])
            + self.factory.create([b"bar"])
            + self.factory.create([b"baz", b"qux"])
        )

        # feed second unfragmented
        stream.add_chunk(chunks[1])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        # feed third partial
        stream.add_chunk(chunks[2])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        # feed first unfragmented
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(
            list(stream.pop_messages()), [(456, 123, b"foo"), (456, 123, b"bar")]
        )
        self.assertEqual(stream.reassembly, [chunks[2]])
        self.assertEqual(stream.sequence_number, 2)

    def test_fragments_in_order(self) -> None:
        stream = InboundStream()
        chunks = self.factory.create([b"foo", b"bar", b"baz"])

        # feed first chunk
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[0]])
        self.assertEqual(stream.sequence_number, 0)

        # feed second chunk
        stream.add_chunk(chunks[1])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        # feed third chunk
        stream.add_chunk(chunks[2])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"foobarbaz")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 1)

    def test_fragments_out_of_order(self) -> None:
        stream = InboundStream()
        chunks = self.factory.create([b"foo", b"bar", b"baz"])

        # feed third chunk
        stream.add_chunk(chunks[2])
        self.assertEqual(stream.reassembly, [chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        # feed first chunk
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        # feed second chunk
        stream.add_chunk(chunks[1])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"foobarbaz")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 1)

    def test_unordered_no_fragments(self) -> None:
        stream = InboundStream()
        chunks = (
            self.factory.create([b"foo"], ordered=False)
            + self.factory.create([b"bar"], ordered=False)
            + self.factory.create([b"baz"], ordered=False)
        )

        # feed second unfragmented
        stream.add_chunk(chunks[1])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"bar")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 0)

        # feed third unfragmented
        stream.add_chunk(chunks[2])
        self.assertEqual(stream.reassembly, [chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"baz")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 0)

        # feed first unfragmented
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"foo")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 0)

    def test_unordered_with_fragments(self) -> None:
        stream = InboundStream()
        chunks = (
            self.factory.create([b"foo", b"bar"], ordered=False)
            + self.factory.create([b"baz"], ordered=False)
            + self.factory.create([b"qux", b"quux", b"corge"], ordered=False)
        )

        # feed second fragment of first message
        stream.add_chunk(chunks[1])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        # feed second message
        stream.add_chunk(chunks[2])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"baz")])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        # feed first fragment of third message
        stream.add_chunk(chunks[3])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[3]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[3]])
        self.assertEqual(stream.sequence_number, 0)

        # feed third fragment of third message
        stream.add_chunk(chunks[5])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[3], chunks[5]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[3], chunks[5]])
        self.assertEqual(stream.sequence_number, 0)

        # feed second fragment of third message
        stream.add_chunk(chunks[4])
        self.assertEqual(
            stream.reassembly, [chunks[1], chunks[3], chunks[4], chunks[5]]
        )
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"quxquuxcorge")])
        self.assertEqual(stream.reassembly, [chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        # feed first fragment of first message
        stream.add_chunk(chunks[0])
        self.assertEqual(stream.reassembly, [chunks[0], chunks[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [(456, 123, b"foobar")])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 0)

    def test_prune_chunks(self) -> None:
        stream = InboundStream()
        factory = ChunkFactory(tsn=100)
        chunks = factory.create([b"foo", b"bar"]) + factory.create([b"baz", b"qux"])

        for i in [1, 2]:
            stream.add_chunk(chunks[i])
            self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 0)

        stream.sequence_number = 2
        self.assertEqual(list(stream.pop_messages()), [])
        self.assertEqual(stream.reassembly, [chunks[1], chunks[2]])
        self.assertEqual(stream.sequence_number, 2)

        self.assertEqual(stream.prune_chunks(101), 3)
        self.assertEqual(stream.reassembly, [chunks[2]])
        self.assertEqual(stream.sequence_number, 2)


class SctpUtilTest(TestCase):
    def test_tsn_minus_one(self) -> None:
        self.assertEqual(tsn_minus_one(0), 4294967295)
        self.assertEqual(tsn_minus_one(1), 0)
        self.assertEqual(tsn_minus_one(4294967294), 4294967293)
        self.assertEqual(tsn_minus_one(4294967295), 4294967294)

    def test_tsn_plus_one(self) -> None:
        self.assertEqual(tsn_plus_one(0), 1)
        self.assertEqual(tsn_plus_one(1), 2)
        self.assertEqual(tsn_plus_one(4294967294), 4294967295)
        self.assertEqual(tsn_plus_one(4294967295), 0)


class RTCSctpTransportTest(TestCase):
    @contextlib.contextmanager
    def assertTimerPreserved(self, client: RTCSctpTransport) -> Generator[None]:
        previous_timer = client._t3_handle
        yield
        self.assertIsNotNone(client._t3_handle)
        self.assertEqual(client._t3_handle, previous_timer)

    @contextlib.contextmanager
    def assertTimerRestarted(self, client: RTCSctpTransport) -> Generator[None]:
        previous_timer = client._t3_handle
        yield
        self.assertIsNotNone(client._t3_handle)
        self.assertNotEqual(client._t3_handle, previous_timer)

    @contextlib.contextmanager
    def assertTimerStopped(self, client: RTCSctpTransport) -> Generator[None]:
        yield
        self.assertIsNone(client._t3_handle)

    @asynctest
    async def test_construct(self) -> None:
        async with dummy_dtls_transport_pair() as (client_transport, _):
            sctpTransport = RTCSctpTransport(client_transport)
            self.assertEqual(sctpTransport.transport, client_transport)
            self.assertEqual(sctpTransport.port, 5000)

    @asynctest
    async def test_construct_invalid_dtls_transport_state(self) -> None:
        dtlsTransport = ClosedDtlsTransport()
        with self.assertRaises(InvalidStateError):
            RTCSctpTransport(dtlsTransport)  # type: ignore

    @asynctest
    async def test_connect_broken_transport(self) -> None:
        """
        Transport with 100% loss never connects.
        """
        loss_pattern = [True]

        async with client_and_server() as (client, server):
            client._rto = 0.1
            set_loss_pattern(client.transport.transport, loss_pattern)

            server._rto = 0.1
            set_loss_pattern(server.transport.transport, loss_pattern)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(client._association_state, RTCSctpTransport.State.CLOSED)
            self.assertEqual(client.state, "closed")
            self.assertEqual(server._association_state, RTCSctpTransport.State.CLOSED)
            self.assertEqual(server.state, "connecting")

    @asynctest
    async def test_connect_lossy_transport(self) -> None:
        """
        Transport with 25% loss eventually connects.
        """
        loss_pattern = [True, False, False, False]

        async with client_and_server() as (client, server):
            client._rto = 0.1
            set_loss_pattern(client.transport.transport, loss_pattern)

            server._rto = 0.1
            set_loss_pattern(server.transport.transport, loss_pattern)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client.state, "connected")
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server.state, "connected")

            # transmit data
            server_queue: asyncio.Queue[tuple[int, int, bytes]] = asyncio.Queue()

            async def mock_receive(stream_id: int, pp_id: int, data: bytes) -> None:
                await server_queue.put((stream_id, pp_id, data))

            server._receive = mock_receive  # type: ignore

            for i in range(20):
                message = (123, i, b"ping")
                await client._send(*message)
                received = await server_queue.get()
                self.assertEqual(received, message)

    @asynctest
    async def test_connect_client_limits_streams(self) -> None:
        async with client_and_server() as (client, server):
            client._inbound_streams_max = 2048
            client._outbound_streams_count = 256
            self.assertEqual(client.maxChannels, None)
            self.assertEqual(server.maxChannels, None)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(client.maxChannels, 256)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 2048)
            self.assertEqual(client._outbound_streams_count, 256)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(server.maxChannels, 256)
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 256)
            self.assertEqual(server._outbound_streams_count, 2048)
            self.assertEqual(server._remote_extensions, [192, 130])

            # client requests additional outbound streams
            param = StreamAddOutgoingParam(
                request_sequence=client._reconfig_request_seq, new_streams=16
            )
            await client._send_reconfig_param(param)

            await asyncio.sleep(0.1)

            self.assertEqual(server.maxChannels, 272)
            self.assertEqual(server._inbound_streams_count, 272)
            self.assertEqual(server._outbound_streams_count, 2048)

    @asynctest
    async def test_connect_server_limits_streams(self) -> None:
        async with client_and_server() as (client, server):
            server._inbound_streams_max = 2048
            server._outbound_streams_count = 256

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(client.maxChannels, 256)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 256)
            self.assertEqual(client._outbound_streams_count, 2048)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(server.maxChannels, 256)
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 2048)
            self.assertEqual(server._outbound_streams_count, 256)
            self.assertEqual(server._remote_extensions, [192, 130])

            await asyncio.sleep(0.1)

    @asynctest
    async def test_connect_then_client_creates_data_channel(self) -> None:
        async with client_and_server() as (client, server):
            self.assertFalse(client.is_server)
            self.assertEqual(client.maxChannels, None)
            self.assertTrue(server.is_server)
            self.assertEqual(server.maxChannels, None)

            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(client.maxChannels, 65535)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(server.maxChannels, 65535)
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel
            channel = RTCDataChannel(client, RTCDataChannelParameters(label="chat"))
            self.assertEqual(channel.id, None)
            self.assertEqual(channel.label, "chat")

            await asyncio.sleep(0.1)
            self.assertEqual(channel.id, 1)
            self.assertEqual(channel.label, "chat")
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 1)
            self.assertEqual(server_channels[0].id, 1)
            self.assertEqual(server_channels[0].label, "chat")

    @asynctest
    async def test_connect_then_client_creates_data_channel_with_custom_id(
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel
            channel = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", id=100)
            )
            self.assertEqual(channel.id, 100)
            self.assertEqual(channel.label, "chat")

            # create second data channel
            channel2 = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", id=101)
            )
            self.assertEqual(channel2.id, 101)
            self.assertEqual(channel2.label, "chat")

            await asyncio.sleep(0.1)
            self.assertEqual(channel.id, 100)
            self.assertEqual(channel.label, "chat")
            self.assertEqual(channel2.id, 101)
            self.assertEqual(channel2.label, "chat")
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 2)
            self.assertEqual(server_channels[0].id, 100)
            self.assertEqual(server_channels[0].label, "chat")
            self.assertEqual(server_channels[1].id, 101)
            self.assertEqual(server_channels[1].label, "chat")

    @asynctest
    async def test_connect_then_client_creates_data_channel_with_custom_id_and_then_normal(  # noqa: E501
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel
            channel = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", id=1)
            )
            self.assertEqual(channel.id, 1)
            self.assertEqual(channel.label, "chat")

            # create second data channel
            channel2 = RTCDataChannel(client, RTCDataChannelParameters(label="chat"))
            self.assertEqual(channel2.id, None)
            self.assertEqual(channel2.label, "chat")

            await asyncio.sleep(0.1)
            self.assertEqual(channel.id, 1)
            self.assertEqual(channel.label, "chat")
            self.assertEqual(channel2.id, 3)
            self.assertEqual(channel2.label, "chat")
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 2)
            self.assertEqual(server_channels[0].id, 1)
            self.assertEqual(server_channels[0].label, "chat")
            self.assertEqual(server_channels[1].id, 3)
            self.assertEqual(server_channels[1].label, "chat")

    @asynctest
    async def test_connect_then_client_creates_second_data_channel_with_custom_already_used_id(  # noqa: E501
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel
            channel = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", id=100)
            )
            self.assertEqual(channel.id, 100)
            self.assertEqual(channel.label, "chat")

            # create second data channel with the same id
            self.assertRaises(
                ValueError,
                lambda: RTCDataChannel(
                    client, RTCDataChannelParameters(label="chat", id=100)
                ),
            )

            await asyncio.sleep(0.1)
            self.assertEqual(channel.id, 100)
            self.assertEqual(channel.label, "chat")
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 1)
            self.assertEqual(server_channels[0].id, 100)
            self.assertEqual(server_channels[0].label, "chat")

    @asynctest
    async def test_connect_then_client_creates_negotiated_data_channel_without_id(
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel
            self.assertRaises(
                ValueError,
                lambda: RTCDataChannel(
                    client, RTCDataChannelParameters(label="chat", negotiated=True)
                ),
            )

            await asyncio.sleep(0.1)

    @asynctest
    async def test_connect_then_client_and_server_creates_negotiated_data_channel(
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel for client
            channel_client = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", negotiated=True, id=100)
            )
            self.assertEqual(channel_client.id, 100)
            self.assertEqual(channel_client.label, "chat")

            # create data channel for server
            channel_server = RTCDataChannel(
                server, RTCDataChannelParameters(label="chat", negotiated=True, id=100)
            )
            self.assertEqual(channel_server.id, 100)
            self.assertEqual(channel_server.label, "chat")

            await asyncio.sleep(0.1)
            self.assertEqual(channel_client.id, 100)
            self.assertEqual(channel_client.label, "chat")
            self.assertEqual(channel_server.id, 100)
            self.assertEqual(channel_server.label, "chat")
            # both arrays should be 0 as they track data channels created by event
            #   which is not the case in out-of-band
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 0)

    @asynctest
    async def test_connect_then_client_creates_negotiated_data_channel_with_used_id(
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel for client
            channel_client = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", negotiated=True, id=100)
            )
            self.assertEqual(channel_client.id, 100)
            self.assertEqual(channel_client.label, "chat")

            self.assertRaises(
                ValueError,
                lambda: RTCDataChannel(
                    client,
                    RTCDataChannelParameters(label="chat", negotiated=True, id=100),
                ),
            )

            await asyncio.sleep(0.1)
            self.assertEqual(channel_client.id, 100)
            self.assertEqual(channel_client.label, "chat")
            # both arrays should be 0 as they track data channels created by event
            #   which is not the case in out-of-band
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 0)

    @asynctest
    async def test_connect_then_client_and_server_creates_negotiated_data_channel_before_transport(  # noqa: E501
        self,
    ) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            self.assertEqual(client._association_state, RTCSctpTransport.State.CLOSED)
            self.assertEqual(server._association_state, RTCSctpTransport.State.CLOSED)

            # create data channel for client
            channel_client = RTCDataChannel(
                client, RTCDataChannelParameters(label="chat", negotiated=True, id=100)
            )
            self.assertEqual(channel_client.id, 100)
            self.assertEqual(channel_client.label, "chat")
            self.assertEqual(channel_client.readyState, "connecting")

            # create data channel for server
            channel_server = RTCDataChannel(
                server, RTCDataChannelParameters(label="chat", negotiated=True, id=100)
            )
            self.assertEqual(channel_server.id, 100)
            self.assertEqual(channel_server.label, "chat")
            self.assertEqual(channel_server.readyState, "connecting")

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._inbound_streams_count, 65535)
            self.assertEqual(client._outbound_streams_count, 65535)
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._inbound_streams_count, 65535)
            self.assertEqual(server._outbound_streams_count, 65535)
            self.assertEqual(server._remote_extensions, [192, 130])

            self.assertEqual(channel_client.readyState, "open")
            self.assertEqual(channel_server.readyState, "open")

            await asyncio.sleep(0.1)
            self.assertEqual(channel_client.id, 100)
            self.assertEqual(channel_client.label, "chat")
            self.assertEqual(channel_server.id, 100)
            self.assertEqual(channel_server.label, "chat")
            # both arrays should be 0 as they track data channels created by event
            #   which is not the case in out-of-band
            self.assertEqual(len(client_channels), 0)
            self.assertEqual(len(server_channels), 0)

    @asynctest
    async def test_connect_then_server_creates_data_channel(self) -> None:
        async with client_and_server() as (client, server):
            client_channels = track_channels(client)
            server_channels = track_channels(server)

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._remote_extensions, [192, 130])
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._remote_extensions, [192, 130])

            # create data channel
            channel = RTCDataChannel(server, RTCDataChannelParameters(label="chat"))
            self.assertEqual(channel.id, None)
            self.assertEqual(channel.label, "chat")

            await asyncio.sleep(0.1)
            self.assertEqual(len(client_channels), 1)
            self.assertEqual(client_channels[0].id, 0)
            self.assertEqual(client_channels[0].label, "chat")
            self.assertEqual(len(server_channels), 0)

    @patch("aiortc.rtcsctptransport.logger.isEnabledFor")
    @asynctest
    async def test_connect_with_logging(self, mock_is_enabled_for: MagicMock) -> None:
        mock_is_enabled_for.return_value = True

        async with client_and_server() as (client, server):
            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )

    @asynctest
    async def test_connect_with_partial_reliability(self) -> None:
        async with client_and_server() as (client, server):
            client._local_partial_reliability = True
            server._local_partial_reliability = False

            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(client._remote_extensions, [130])
            self.assertEqual(client._remote_partial_reliability, False)
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(server._remote_extensions, [192, 130])
            self.assertEqual(server._remote_partial_reliability, True)

    @asynctest
    async def test_abrupt_disconnect(self) -> None:
        """
        Abrupt disconnect causes sending ABORT chunk to fail.
        """
        async with client_and_server() as (client, server):
            # connect
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await wait_for_outcome(client, server)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.ESTABLISHED
            )
            self.assertEqual(
                server._association_state, RTCSctpTransport.State.ESTABLISHED
            )

            # break connection
            await client.transport.stop()
            await server.transport.stop()
            await asyncio.sleep(0)  # FIXME

    @asynctest
    async def test_garbage(self) -> None:
        async with client_and_server() as (_, server):
            await server.start(RTCSctpCapabilities(maxMessageSize=65536), 5000)
            await server._handle_data(b"garbage")

            # check outcome
            await asyncio.sleep(0.1)
            self.assertEqual(server._association_state, RTCSctpTransport.State.CLOSED)

    @asynctest
    async def test_bad_verification_tag(self) -> None:
        # verification tag is 12345 instead of 0
        data = load("sctp_init_bad_verification.bin")

        async with client_and_server() as (_, server):
            await server.start(RTCSctpCapabilities(maxMessageSize=65536), 5000)
            await server._handle_data(data)

            # check outcome
            await asyncio.sleep(0.1)
            self.assertEqual(server._association_state, RTCSctpTransport.State.CLOSED)

    @asynctest
    async def test_bad_cookie(self) -> None:
        async with client_and_server() as (client, server):
            # corrupt cookie
            real_send_chunk = client._send_chunk

            async def mock_send_chunk(chunk: Chunk) -> None:
                if isinstance(chunk, CookieEchoChunk):
                    chunk.body = b"garbage"
                return await real_send_chunk(chunk)

            client._send_chunk = mock_send_chunk  # type: ignore

            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await asyncio.sleep(0.1)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.COOKIE_ECHOED
            )
            self.assertEqual(server._association_state, RTCSctpTransport.State.CLOSED)

    @asynctest
    async def test_maybe_abandon(self) -> None:
        async with client_standalone() as client:
            client._local_tsn = 0
            client._send_chunk = noop_send_chunk  # type: ignore

            # send 3 chunks
            await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 3)
            self.assertEqual(outstanding_tsns(client), [0, 1, 2])
            self.assertEqual(queued_tsns(client), [])
            for chunk in client._outbound_queue:
                self.assertEqual(chunk._abandoned, False)

            # try abandon middle chunk
            client._maybe_abandon(client._sent_queue[1])
            for chunk in client._outbound_queue:
                self.assertEqual(chunk._abandoned, False)

    @asynctest
    async def test_maybe_abandon_max_retransmits(self) -> None:
        async with client_standalone() as client:
            client._local_tsn = 1
            client._last_sacked_tsn = 0
            client._advanced_peer_ack_tsn = 0
            client._send_chunk = noop_send_chunk  # type: ignore

            # send 3 chunks
            await client._send(
                123, 456, b"M" * USERDATA_MAX_LENGTH * 3, max_retransmits=0
            )
            self.assertEqual(outstanding_tsns(client), [1, 2, 3])
            self.assertEqual(queued_tsns(client), [])
            self.assertEqual(client._local_tsn, 4)
            self.assertEqual(client._advanced_peer_ack_tsn, 0)
            for chunk in client._outbound_queue:
                self.assertEqual(chunk._abandoned, False)

            # try abandon middle chunk
            client._maybe_abandon(client._sent_queue[1])
            for chunk in client._outbound_queue:
                self.assertEqual(chunk._abandoned, True)

            # try abandon middle chunk (again)
            client._maybe_abandon(client._sent_queue[1])
            for chunk in client._outbound_queue:
                self.assertEqual(chunk._abandoned, True)

            # update advanced peer ack point
            client._update_advanced_peer_ack_point()
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])
            self.assertEqual(client._advanced_peer_ack_tsn, 3)

            # check forward TSN
            self.assertIsNotNone(client._forward_tsn_chunk)
            self.assertEqual(client._forward_tsn_chunk.cumulative_tsn, 3)
            self.assertEqual(client._forward_tsn_chunk.streams, [(123, 0)])

            # transmit
            client._t3_cancel()
            await client._transmit()
            self.assertIsNone(client._forward_tsn_chunk)
            self.assertIsNotNone(client._t3_handle)

    @asynctest
    async def test_stale_cookie(self) -> None:
        mock_timestamp_values = [0, 61]

        def mock_timestamp() -> int:
            return mock_timestamp_values.pop(0)

        async with client_and_server() as (client, server):
            server._get_timestamp = mock_timestamp  # type: ignore
            await server.start(client.getCapabilities(), client.port)
            await client.start(server.getCapabilities(), server.port)

            # check outcome
            await asyncio.sleep(0.1)
            self.assertEqual(client._association_state, RTCSctpTransport.State.CLOSED)
            self.assertEqual(server._association_state, RTCSctpTransport.State.CLOSED)

    @asynctest
    async def test_receive_data(self) -> None:
        async with client_standalone() as client:
            client._last_received_tsn = 0

            # receive chunk
            chunk = DataChunk(flags=(SCTP_DATA_FIRST_FRAG | SCTP_DATA_LAST_FRAG))
            chunk.user_data = b"foo"
            chunk.tsn = 1
            await client._receive_chunk(chunk)

            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set())
            self.assertEqual(client._last_received_tsn, 1)
            client._sack_needed = False

            # receive chunk again
            await client._receive_chunk(chunk)
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [1])
            self.assertEqual(client._sack_misordered, set())
            self.assertEqual(client._last_received_tsn, 1)

    @asynctest
    async def test_receive_data_out_of_order(self) -> None:
        async with client_standalone() as client:
            client._last_received_tsn = 0

            # build chunks
            chunks = []

            chunk = DataChunk(flags=SCTP_DATA_FIRST_FRAG)
            chunk.user_data = b"foo"
            chunk.tsn = 1
            chunks.append(chunk)

            chunk = DataChunk()
            chunk.user_data = b"bar"
            chunk.tsn = 2
            chunks.append(chunk)

            chunk = DataChunk(flags=SCTP_DATA_LAST_FRAG)
            chunk.user_data = b"baz"
            chunk.tsn = 3
            chunks.append(chunk)

            # receive first chunk
            await client._receive_chunk(chunks[0])
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set())
            self.assertEqual(client._last_received_tsn, 1)
            client._sack_needed = False

            # receive last chunk
            await client._receive_chunk(chunks[2])
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set([3]))
            self.assertEqual(client._last_received_tsn, 1)
            client._sack_needed = False

            # receive middle chunk
            await client._receive_chunk(chunks[1])
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set([]))
            self.assertEqual(client._last_received_tsn, 3)
            client._sack_needed = False

            # receive last chunk again
            await client._receive_chunk(chunks[2])
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [3])
            self.assertEqual(client._sack_misordered, set([]))
            self.assertEqual(client._last_received_tsn, 3)
            client._sack_needed = False

    @asynctest
    async def test_receive_forward_tsn(self) -> None:
        received = []

        async def mock_receive(stream_id: int, pp_id: int, data: bytes) -> None:
            received.append((stream_id, pp_id, data))

        async with client_standalone() as client:
            client._last_received_tsn = 101
            client._receive = mock_receive  # type: ignore

            factory = ChunkFactory(tsn=102)
            chunks = (
                factory.create([b"foo"])
                + factory.create([b"baz"])
                + factory.create([b"qux"])
                + factory.create([b"quux"])
                + factory.create([b"corge"])
                + factory.create([b"grault"])
            )

            # receive chunks with gaps
            for i in [0, 2, 3, 5]:
                await client._receive_chunk(chunks[i])

            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set([104, 105, 107]))
            self.assertEqual(client._last_received_tsn, 102)
            self.assertEqual(received, [(456, 123, b"foo")])
            received.clear()
            client._sack_needed = False

            # receive forward tsn
            chunk = ForwardTsnChunk()
            chunk.cumulative_tsn = 103
            chunk.streams = [(456, 1)]
            await client._receive_chunk(chunk)
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set([107]))
            self.assertEqual(client._last_received_tsn, 105)
            self.assertEqual(received, [(456, 123, b"qux"), (456, 123, b"quux")])
            received.clear()
            client._sack_needed = False

            # receive forward tsn again
            await client._receive_chunk(chunk)
            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set([107]))
            self.assertEqual(client._last_received_tsn, 105)
            self.assertEqual(received, [])
            client._sack_needed = False

            # receive chunk
            await client._receive_chunk(chunks[4])

            self.assertEqual(client._sack_needed, True)
            self.assertEqual(client._sack_duplicates, [])
            self.assertEqual(client._sack_misordered, set())
            self.assertEqual(client._last_received_tsn, 107)
            self.assertEqual(received, [(456, 123, b"corge"), (456, 123, b"grault")])
            received.clear()
            client._sack_needed = False

    @asynctest
    async def test_receive_heartbeat(self) -> None:
        ack = None

        async def mock_send_chunk(chunk: Chunk) -> None:
            nonlocal ack
            assert isinstance(chunk, HeartbeatAckChunk)
            ack = chunk

        async with client_standalone() as client:
            client._send_chunk = mock_send_chunk  # type: ignore

            # receive heartbeat
            chunk = HeartbeatChunk()
            chunk.params.append((1, b"\x01\x02\x03\x04"))
            await client._receive_chunk(chunk)

            # check response
            self.assertIsInstance(ack, HeartbeatAckChunk)
            self.assertEqual(ack.params, [(1, b"\x01\x02\x03\x04")])

    @asynctest
    async def test_receive_sack_discard(self) -> None:
        async with client_standalone() as client:
            client._last_received_tsn = 0

            # receive sack
            sack_point = client._last_sacked_tsn
            chunk = SackChunk()
            chunk.cumulative_tsn = tsn_minus_one(sack_point)
            await client._receive_chunk(chunk)

            # sack point must not changed
            self.assertEqual(client._last_sacked_tsn, sack_point)

    @asynctest
    async def test_receive_shutdown(self) -> None:
        async with client_standalone() as client:
            client._last_received_tsn = 0
            client._send_chunk = noop_send_chunk  # type: ignore
            client._set_state(RTCSctpTransport.State.ESTABLISHED)

            # receive shutdown
            chunk = ShutdownChunk()
            chunk.cumulative_tsn = tsn_minus_one(client._last_sacked_tsn)
            await client._receive_chunk(chunk)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.SHUTDOWN_ACK_SENT
            )

            # receive shutdown complete
            chunk2 = ShutdownCompleteChunk()
            await client._receive_chunk(chunk2)
            self.assertEqual(client._association_state, RTCSctpTransport.State.CLOSED)

    @asynctest
    async def test_mark_received(self) -> None:
        async with client_standalone() as client:
            client._last_received_tsn = 0

            # receive 1
            self.assertFalse(client._mark_received(1))
            self.assertEqual(client._last_received_tsn, 1)
            self.assertEqual(client._sack_misordered, set())

            # receive 3
            self.assertFalse(client._mark_received(3))
            self.assertEqual(client._last_received_tsn, 1)
            self.assertEqual(client._sack_misordered, set([3]))

            # receive 4
            self.assertFalse(client._mark_received(4))
            self.assertEqual(client._last_received_tsn, 1)
            self.assertEqual(client._sack_misordered, set([3, 4]))

            # receive 6
            self.assertFalse(client._mark_received(6))
            self.assertEqual(client._last_received_tsn, 1)
            self.assertEqual(client._sack_misordered, set([3, 4, 6]))

            # receive 2
            self.assertFalse(client._mark_received(2))
            self.assertEqual(client._last_received_tsn, 4)
            self.assertEqual(client._sack_misordered, set([6]))

    @asynctest
    async def test_send_sack(self) -> None:
        sack = None

        async def mock_send_chunk(chunk: Chunk) -> None:
            nonlocal sack
            assert isinstance(chunk, SackChunk)
            sack = chunk

        async with client_standalone() as client:
            client._last_received_tsn = 123
            client._send_chunk = mock_send_chunk  # type: ignore

            await client._send_sack()
            self.assertIsNotNone(sack)
            self.assertEqual(sack.duplicates, [])
            self.assertEqual(sack.gaps, [])
            self.assertEqual(sack.cumulative_tsn, 123)

    @asynctest
    async def test_send_sack_with_duplicates(self) -> None:
        sack = None

        async def mock_send_chunk(chunk: Chunk) -> None:
            nonlocal sack
            assert isinstance(chunk, SackChunk)
            sack = chunk

        async with client_standalone() as client:
            client._last_received_tsn = 123
            client._sack_duplicates = [125, 127]
            client._send_chunk = mock_send_chunk  # type: ignore

            await client._send_sack()
            self.assertIsNotNone(sack)
            self.assertEqual(sack.duplicates, [125, 127])
            self.assertEqual(sack.gaps, [])
            self.assertEqual(sack.cumulative_tsn, 123)

    @asynctest
    async def test_send_sack_with_gaps(self) -> None:
        sack = None

        async def mock_send_chunk(chunk: SackChunk) -> None:
            nonlocal sack
            sack = chunk

        async with client_standalone() as client:
            client._last_received_tsn = 12
            client._sack_misordered = {14, 15, 17}
            client._send_chunk = mock_send_chunk  # type: ignore

            await client._send_sack()
            self.assertIsNotNone(sack)
            self.assertEqual(sack.duplicates, [])
            self.assertEqual(sack.gaps, [(2, 3), (5, 5)])
            self.assertEqual(sack.cumulative_tsn, 12)

    @asynctest
    async def test_send_data(self) -> None:
        async with client_standalone() as client:
            client._local_tsn = 0
            client._send_chunk = noop_send_chunk  # type: ignore

            # no data
            await client._transmit()
            self.assertIsNone(client._t3_handle)
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])
            self.assertEqual(client._outbound_stream_seq, {})

            # 1 chunk
            await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH)
            self.assertIsNotNone(client._t3_handle)
            self.assertEqual(outstanding_tsns(client), [0])
            self.assertEqual(queued_tsns(client), [])
            self.assertEqual(client._outbound_stream_seq, {123: 1})

    @asynctest
    async def test_send_data_unordered(self) -> None:
        async with client_standalone() as client:
            client._local_tsn = 0
            client._send_chunk = noop_send_chunk  # type: ignore

            # 1 chunk
            await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH, ordered=False)
            self.assertIsNotNone(client._t3_handle)
            self.assertEqual(outstanding_tsns(client), [0])
            self.assertEqual(queued_tsns(client), [])
            self.assertEqual(client._outbound_stream_seq, {})

    @asynctest
    async def test_send_data_congestion_control(self) -> None:
        sent_tsns = []

        async def mock_send_chunk(chunk: Chunk) -> None:
            assert isinstance(chunk, DataChunk)
            sent_tsns.append(chunk.tsn)

        async with client_standalone() as client:
            client._cwnd = 4800
            client._last_sacked_tsn = 4294967295
            client._local_tsn = 0
            client._ssthresh = 4800
            client._send_chunk = mock_send_chunk  # type: ignore

            # queue 16 chunks, but cwnd only allows 4
            await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 16)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2, 3])
            self.assertEqual(
                queued_tsns(client), [4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
            )

            # SACK comes in acknowledging 2 chunks
            sack = SackChunk()
            sack.cumulative_tsn = 1
            await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 6000)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6])
            self.assertEqual(outstanding_tsns(client), [2, 3, 4, 5, 6])
            self.assertEqual(queued_tsns(client), [7, 8, 9, 10, 11, 12, 13, 14, 15])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 3
            await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 6000)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 8])
            self.assertEqual(outstanding_tsns(client), [4, 5, 6, 7, 8])
            self.assertEqual(queued_tsns(client), [9, 10, 11, 12, 13, 14, 15])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 5
            await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 6000)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
            self.assertEqual(outstanding_tsns(client), [6, 7, 8, 9, 10])
            self.assertEqual(queued_tsns(client), [11, 12, 13, 14, 15])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 7
            await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 7200)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 7200)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
            self.assertEqual(outstanding_tsns(client), [8, 9, 10, 11, 12, 13])
            self.assertEqual(queued_tsns(client), [14, 15])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 9
            await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 7200)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 7200)
            self.assertEqual(
                sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
            )
            self.assertEqual(outstanding_tsns(client), [10, 11, 12, 13, 14, 15])
            self.assertEqual(queued_tsns(client), [])

    @asynctest
    async def test_send_data_slow_start(self) -> None:
        sent_tsns = []

        async def mock_send_chunk(chunk: Chunk) -> None:
            assert isinstance(chunk, DataChunk)
            sent_tsns.append(chunk.tsn)

        async with client_standalone() as client:
            client._last_sacked_tsn = 4294967295
            client._local_tsn = 0
            client._ssthresh = 131072
            client._send_chunk = mock_send_chunk  # type: ignore

            # queue 8 chunks, but cwnd only allows 3
            with self.assertTimerRestarted(client):
                await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 8)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2])
            self.assertEqual(queued_tsns(client), [3, 4, 5, 6, 7])

            # SACK comes in acknowledging 2 chunks
            sack = SackChunk()
            sack.cumulative_tsn = 1
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5])
            self.assertEqual(outstanding_tsns(client), [2, 3, 4, 5])
            self.assertEqual(queued_tsns(client), [6, 7])

            # SACK sack comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 3
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 5
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 2400)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging final chunks
            sack = SackChunk()
            sack.cumulative_tsn = 7
            with self.assertTimerStopped(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 0)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])

    @asynctest
    async def test_send_data_with_gap(self) -> None:
        sent_tsns = []

        async def mock_send_chunk(chunk: Chunk) -> None:
            assert isinstance(chunk, DataChunk)
            sent_tsns.append(chunk.tsn)

        async with client_standalone() as client:
            client._last_sacked_tsn = 4294967295
            client._local_tsn = 0
            client._ssthresh = 131072
            client._send_chunk = mock_send_chunk  # type: ignore

            # queue 8 chunks, but cwnd only allows 3
            with self.assertTimerRestarted(client):
                await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 8)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2])
            self.assertEqual(queued_tsns(client), [3, 4, 5, 6, 7])

            # SACK comes in acknowledging chunks 0 and 2
            sack = SackChunk()
            sack.cumulative_tsn = 0
            sack.gaps = [(2, 2)]  # TSN 1 is missing
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5])
            self.assertEqual(outstanding_tsns(client), [1, 2, 3, 4, 5])
            self.assertEqual(queued_tsns(client), [6, 7])

            # SACK comes in acknowledging chunks 1 and 3
            sack = SackChunk()
            sack.cumulative_tsn = 3
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 5
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 2400)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging final chunks
            sack = SackChunk()
            sack.cumulative_tsn = 7
            with self.assertTimerStopped(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 6000)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 0)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])

    @asynctest
    async def test_send_data_with_gap_1_retransmit(self) -> None:
        sent_tsns = []

        async def mock_send_chunk(chunk: Chunk) -> None:
            assert isinstance(chunk, DataChunk)
            sent_tsns.append(chunk.tsn)

        async with client_standalone() as client:
            client._last_sacked_tsn = 4294967295
            client._local_tsn = 0
            client._ssthresh = 131072
            client._send_chunk = mock_send_chunk  # type: ignore

            # queue 8 chunks, but cwnd only allows 3
            with self.assertTimerRestarted(client):
                await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 8)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2])
            self.assertEqual(queued_tsns(client), [3, 4, 5, 6, 7])

            # SACK comes in acknowledging chunks 0 and 2
            sack = SackChunk()
            sack.cumulative_tsn = 0
            sack.gaps = [(2, 2)]  # TSN 1 is missing
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5])
            self.assertEqual(outstanding_tsns(client), [1, 2, 3, 4, 5])
            self.assertEqual(queued_tsns(client), [6, 7])

            # SACK comes in acknowledging chunks 3 and 4
            sack = SackChunk()
            sack.cumulative_tsn = 0
            sack.gaps = [(2, 4)]  # TSN 1 is missing
            with self.assertTimerPreserved(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging 2 more chunks
            sack = SackChunk()
            sack.cumulative_tsn = 0
            sack.gaps = [(2, 6)]  # TSN 1 is missing
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, 7)
            self.assertEqual(client._flight_size, 2400)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 1])
            self.assertEqual(outstanding_tsns(client), [1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging final chunks
            sack = SackChunk()
            sack.cumulative_tsn = 7
            with self.assertTimerStopped(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 0)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 1])
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])

    @asynctest
    async def test_send_data_with_gap_2_retransmit(self) -> None:
        sent_tsns = []

        async def mock_send_chunk(chunk: Chunk) -> None:
            assert isinstance(chunk, DataChunk)
            sent_tsns.append(chunk.tsn)

        async with client_standalone() as client:
            client._last_sacked_tsn = 4294967295
            client._local_tsn = 0
            client._ssthresh = 131072
            client._send_chunk = mock_send_chunk  # type: ignore

            # queue 8 chunks, but cwnd only allows 3
            with self.assertTimerRestarted(client):
                await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 8)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2])
            self.assertEqual(queued_tsns(client), [3, 4, 5, 6, 7])

            # SACK comes in acknowledging chunk 2
            sack = SackChunk()
            sack.cumulative_tsn = 4294967295
            sack.gaps = [(3, 3)]  # TSN 0 and 1 are missing
            with self.assertTimerPreserved(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2, 3])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2, 3])
            self.assertEqual(queued_tsns(client), [4, 5, 6, 7])

            # SACK comes in acknowledging chunk 3
            sack = SackChunk()
            sack.cumulative_tsn = 4294967295
            sack.gaps = [(3, 4)]  # TSN 0 and 1 are missing
            with self.assertTimerPreserved(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2, 3, 4])
            self.assertEqual(queued_tsns(client), [5, 6, 7])

            # SACK comes in acknowledging chunk 4
            sack = SackChunk()
            sack.cumulative_tsn = 4294967295
            sack.gaps = [(3, 5)]  # TSN 0 and 1 are missing
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, 4)
            self.assertEqual(client._flight_size, 2400)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 0, 1])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2, 3, 4])
            self.assertEqual(queued_tsns(client), [5, 6, 7])

            # SACK comes in acknowledging all chunks up to 4
            sack = SackChunk()
            sack.cumulative_tsn = 4
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 0, 1, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging final chunks
            sack = SackChunk()
            sack.cumulative_tsn = 7
            with self.assertTimerStopped(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 0)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 0, 1, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])

    @asynctest
    async def test_send_data_with_gap_3_retransmit(self) -> None:
        sent_tsns = []

        async def mock_send_chunk(chunk: Chunk) -> None:
            assert isinstance(chunk, DataChunk)
            sent_tsns.append(chunk.tsn)

        async with client_standalone() as client:
            client._last_sacked_tsn = 4294967295
            client._local_tsn = 0
            client._ssthresh = 131072
            client._send_chunk = mock_send_chunk  # type: ignore

            # queue 8 chunks, but cwnd only allows 3
            with self.assertTimerRestarted(client):
                await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH * 8)

            self.assertEqual(client._cwnd, 3600)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2])
            self.assertEqual(outstanding_tsns(client), [0, 1, 2])
            self.assertEqual(queued_tsns(client), [3, 4, 5, 6, 7])

            # SACK comes in acknowledging chunks 0 and 1
            sack = SackChunk()
            sack.cumulative_tsn = 1
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5])
            self.assertEqual(outstanding_tsns(client), [2, 3, 4, 5])
            self.assertEqual(queued_tsns(client), [6, 7])

            # SACK comes in acknowledging chunk 5
            sack = SackChunk()
            sack.cumulative_tsn = 1
            sack.gaps = [(4, 4)]  # TSN 2, 3 and 4 are missing
            with self.assertTimerPreserved(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6])
            self.assertEqual(outstanding_tsns(client), [2, 3, 4, 5, 6])
            self.assertEqual(queued_tsns(client), [7])

            # SACK comes in acknowledging chunk 6
            sack = SackChunk()
            sack.cumulative_tsn = 1
            sack.gaps = [(4, 5)]  # TSN 2, 3 and 4 are missing
            with self.assertTimerPreserved(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7])
            self.assertEqual(outstanding_tsns(client), [2, 3, 4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # artificially raise flight size to hit cwnd
            client._flight_size += 2400

            # SACK comes in acknowledging chunk 7
            sack = SackChunk()
            sack.cumulative_tsn = 1
            sack.gaps = [(4, 6)]  # TSN 2, 3 and 4 are missing
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, 7)
            self.assertEqual(client._flight_size, 4800)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 2, 3])
            self.assertEqual(outstanding_tsns(client), [2, 3, 4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in acknowledging all chunks up to 3, and 5, 6, 7
            sack = SackChunk()
            sack.cumulative_tsn = 3
            sack.gaps = [(2, 4)]  # TSN 4 is missing
            with self.assertTimerRestarted(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, 7)
            self.assertEqual(client._flight_size, 3600)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 2, 3, 4])
            self.assertEqual(outstanding_tsns(client), [4, 5, 6, 7])
            self.assertEqual(queued_tsns(client), [])

            # SACK comes in ackowledging all chunks
            sack = SackChunk()
            sack.cumulative_tsn = 7
            with self.assertTimerStopped(client):
                await client._receive_chunk(sack)

            self.assertEqual(client._cwnd, 4800)
            self.assertEqual(client._fast_recovery_exit, None)
            self.assertEqual(client._flight_size, 2400)
            self.assertEqual(sent_tsns, [0, 1, 2, 3, 4, 5, 6, 7, 2, 3, 4])
            self.assertEqual(outstanding_tsns(client), [])
            self.assertEqual(queued_tsns(client), [])

    @asynctest
    async def test_t2_expired_when_shutdown_ack_sent(self) -> None:
        async with client_standalone() as client:
            client._send_chunk = noop_send_chunk  # type: ignore

            chunk = ShutdownAckChunk()

            # fails once
            client._set_state(RTCSctpTransport.State.SHUTDOWN_ACK_SENT)
            client._t2_start(chunk)
            client._t2_expired()
            self.assertEqual(client._t2_failures, 1)
            self.assertIsNotNone(client._t2_handle)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.SHUTDOWN_ACK_SENT
            )

            # fails 10 times
            client._t2_failures = 9
            client._t2_expired()
            self.assertEqual(client._t2_failures, 10)
            self.assertIsNotNone(client._t2_handle)
            self.assertEqual(
                client._association_state, RTCSctpTransport.State.SHUTDOWN_ACK_SENT
            )

            # fails 11 times
            client._t2_expired()
            self.assertEqual(client._t2_failures, 11)
            self.assertIsNone(client._t2_handle)
            self.assertEqual(client._association_state, RTCSctpTransport.State.CLOSED)

            # let async code complete
            await asyncio.sleep(0)

    @asynctest
    async def test_t3_expired(self) -> None:
        async def noop_transmit() -> None:
            pass

        async with client_standalone() as client:
            client._local_tsn = 0
            client._send_chunk = noop_send_chunk  # type: ignore

            # 1 chunk
            await client._send(123, 456, b"M" * USERDATA_MAX_LENGTH)
            self.assertIsNotNone(client._t3_handle)
            self.assertEqual(outstanding_tsns(client), [0])
            self.assertEqual(queued_tsns(client), [])

            # t3 expires
            client._transmit = noop_transmit  # type: ignore
            client._t3_expired()
            self.assertIsNone(client._t3_handle)
            self.assertEqual(outstanding_tsns(client), [0])
            self.assertEqual(queued_tsns(client), [])
            for chunk in client._outbound_queue:
                self.assertEqual(chunk._retransmit, True)

            # let async code complete
            await asyncio.sleep(0)
