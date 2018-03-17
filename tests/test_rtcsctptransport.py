import asyncio
from unittest import TestCase

from aiortc.exceptions import InvalidStateError
from aiortc.rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from aiortc.rtcsctptransport import (SCTP_DATA_FIRST_FRAG, SCTP_DATA_LAST_FRAG,
                                     AbortChunk, CookieEchoChunk, DataChunk,
                                     ErrorChunk, HeartbeatAckChunk,
                                     HeartbeatChunk, InboundStream, InitChunk,
                                     Packet, RTCSctpCapabilities,
                                     RTCSctpTransport, SackChunk,
                                     ShutdownChunk, seq_gt, seq_plus_one,
                                     tsn_gt, tsn_gte, tsn_minus_one)

from .utils import dummy_dtls_transport_pair, load, run


def track_channels(transport):
        channels = []

        @transport.on('datachannel')
        def on_datachannel(channel):
            channels.append(channel)

        return channels


async def wait_for_outcome(client, server):
    final = [
        RTCSctpTransport.State.ESTABLISHED,
        RTCSctpTransport.State.CLOSED,
    ]
    for i in range(100):
        if client.state in final and server.state in final:
            break
        await asyncio.sleep(0.1)


class DummyDtlsTransport:
    def __init__(self, state='new'):
        self.state = state


class SctpPacketTest(TestCase):
    def test_parse_init(self):
        data = load('sctp_init.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 0)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], InitChunk))
        self.assertEqual(packet.chunks[0].type, 1)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(len(packet.chunks[0].body), 82)

        self.assertEqual(bytes(packet), data)

    def test_parse_init_invalid_checksum(self):
        data = load('sctp_init.bin')
        data = data[0:8] + b'\x01\x02\x03\x04' + data[12:]
        with self.assertRaises(ValueError) as cm:
            Packet.parse(data)
        self.assertEqual(str(cm.exception), 'SCTP packet has invalid checksum')

    def test_parse_init_truncated_packet_header(self):
        data = load('sctp_init.bin')[0:10]
        with self.assertRaises(ValueError) as cm:
            Packet.parse(data)
        self.assertEqual(str(cm.exception), 'SCTP packet length is less than 12 bytes')

    def test_parse_cookie_echo(self):
        data = load('sctp_cookie_echo.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 1039286925)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], CookieEchoChunk))
        self.assertEqual(packet.chunks[0].type, 10)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(len(packet.chunks[0].body), 8)

        self.assertEqual(bytes(packet), data)

    def test_parse_abort(self):
        data = load('sctp_abort.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 3763951554)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], AbortChunk))
        self.assertEqual(packet.chunks[0].type, 6)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].params, [
            (13, b'Expected B-bit for TSN=4ce1f17f, SID=0001, SSN=0000'),
        ])

        self.assertEqual(bytes(packet), data)

    def test_parse_error(self):
        data = load('sctp_error.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 3763951554)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], ErrorChunk))
        self.assertEqual(packet.chunks[0].type, 9)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].params, [
            (1, b'\x30\x39\x00\x00'),
        ])

        self.assertEqual(bytes(packet), data)

    def test_parse_heartbeat(self):
        data = load('sctp_heartbeat.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 3100082021)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], HeartbeatChunk))
        self.assertEqual(packet.chunks[0].type, 4)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].params, [
            (1, b'\xb5o\xaaZvZ\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00{\x10\x00\x00'
                b'\x004\xeb\x07F\x10\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        ])

        self.assertEqual(bytes(packet), data)

    def test_parse_sack(self):
        data = load('sctp_sack.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 4146048843)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], SackChunk))
        self.assertEqual(packet.chunks[0].type, 3)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].cumulative_tsn, 2222939037)
        self.assertEqual(packet.chunks[0].gaps, [(2, 2), (4, 4)])
        self.assertEqual(packet.chunks[0].duplicates, [2222939041])

        self.assertEqual(bytes(packet), data)

    def test_parse_shutdown(self):
        data = load('sctp_shutdown.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 4019984498)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], ShutdownChunk))
        self.assertEqual(repr(packet.chunks[0]),
                         'ShutdownChunk(flags=0, cumulative_tsn=2696426712)')
        self.assertEqual(packet.chunks[0].type, 7)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].cumulative_tsn, 2696426712)

        self.assertEqual(bytes(packet), data)


class SctpStreamTest(TestCase):
    def setUp(self):
        self.fragmented = []
        self.whole = []

        # fragmented
        chunk = DataChunk(flags=SCTP_DATA_FIRST_FRAG)
        chunk.tsn = 1
        chunk.protocol = 123
        chunk.stream_id = 456
        chunk.user_data = b'foo'
        self.fragmented.append(chunk)

        chunk = DataChunk()
        chunk.protocol = 123
        chunk.stream_id = 456
        chunk.tsn = 2
        chunk.user_data = b'bar'
        self.fragmented.append(chunk)

        chunk = DataChunk(flags=SCTP_DATA_LAST_FRAG)
        chunk.protocol = 123
        chunk.stream_id = 456
        chunk.tsn = 3
        chunk.user_data = b'baz'
        self.fragmented.append(chunk)

        # whole
        chunk = DataChunk(flags=SCTP_DATA_FIRST_FRAG | SCTP_DATA_LAST_FRAG)
        chunk.tsn = 1
        chunk.protocol = 123
        chunk.stream_id = 456
        chunk.stream_seq = 0
        chunk.user_data = b'foo'
        self.whole.append(chunk)

        chunk = DataChunk(flags=SCTP_DATA_FIRST_FRAG | SCTP_DATA_LAST_FRAG)
        chunk.tsn = 2
        chunk.protocol = 123
        chunk.stream_id = 456
        chunk.stream_seq = 1
        chunk.user_data = b'bar'
        self.whole.append(chunk)

        chunk = DataChunk(flags=SCTP_DATA_FIRST_FRAG)
        chunk.tsn = 3
        chunk.protocol = 123
        chunk.stream_id = 456
        chunk.stream_seq = 2
        chunk.user_data = b'baz'
        self.whole.append(chunk)

    def test_duplicate(self):
        stream = InboundStream()

        # feed first chunk
        stream.add_chunk(self.fragmented[0])
        self.assertEqual(stream.reassembly, [self.fragmented[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed first chunk again
        stream.add_chunk(self.fragmented[0])
        self.assertEqual(stream.reassembly, [self.fragmented[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

    def test_whole_in_order(self):
        stream = InboundStream()

        # feed first unfragmented
        stream.add_chunk(self.whole[0])
        self.assertEqual(stream.reassembly, [self.whole[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [
            (456, 123, b'foo'),
        ])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 1)

        # feed second unfragmented
        stream.add_chunk(self.whole[1])
        self.assertEqual(stream.reassembly, [self.whole[1]])
        self.assertEqual(stream.sequence_number, 1)

        self.assertEqual(list(stream.pop_messages()), [
            (456, 123, b'bar'),
        ])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 2)

    def test_whole_out_of_order(self):
        stream = InboundStream()

        # feed second unfragmented
        stream.add_chunk(self.whole[1])
        self.assertEqual(stream.reassembly, [self.whole[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed third partial
        stream.add_chunk(self.whole[2])
        self.assertEqual(stream.reassembly, [self.whole[1], self.whole[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed first unfragmented
        stream.add_chunk(self.whole[0])
        self.assertEqual(stream.reassembly, [self.whole[0], self.whole[1], self.whole[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [
            (456, 123, b'foo'),
            (456, 123, b'bar'),
        ])
        self.assertEqual(stream.reassembly, [self.whole[2]])
        self.assertEqual(stream.sequence_number, 2)

    def test_fragments_in_order(self):
        stream = InboundStream()

        # feed first chunk
        stream.add_chunk(self.fragmented[0])
        self.assertEqual(stream.reassembly, [self.fragmented[0]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed second chunk
        stream.add_chunk(self.fragmented[1])
        self.assertEqual(stream.reassembly, [self.fragmented[0], self.fragmented[1]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed third chunk
        stream.add_chunk(self.fragmented[2])
        self.assertEqual(stream.reassembly, [
            self.fragmented[0], self.fragmented[1], self.fragmented[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [
            (456, 123, b'foobarbaz'),
        ])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 1)

    def test_fragments_out_of_order(self):
        stream = InboundStream()

        # feed third chunk
        stream.add_chunk(self.fragmented[2])
        self.assertEqual(stream.reassembly, [self.fragmented[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed first chunk
        stream.add_chunk(self.fragmented[0])
        self.assertEqual(stream.reassembly, [self.fragmented[0], self.fragmented[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [])

        # feed second chunk
        stream.add_chunk(self.fragmented[1])
        self.assertEqual(stream.reassembly, [
            self.fragmented[0], self.fragmented[1], self.fragmented[2]])
        self.assertEqual(stream.sequence_number, 0)

        self.assertEqual(list(stream.pop_messages()), [
            (456, 123, b'foobarbaz'),
        ])
        self.assertEqual(stream.reassembly, [])
        self.assertEqual(stream.sequence_number, 1)


class SctpUtilTest(TestCase):
    def test_seq_gt(self):
        self.assertFalse(seq_gt(0, 1))
        self.assertFalse(seq_gt(1, 1))
        self.assertTrue(seq_gt(2, 1))
        self.assertTrue(seq_gt(32768, 1))
        self.assertFalse(seq_gt(32769, 1))
        self.assertFalse(seq_gt(65535, 1))

    def test_seq_plus_one(self):
        self.assertEqual(seq_plus_one(0), 1)
        self.assertEqual(seq_plus_one(1), 2)
        self.assertEqual(seq_plus_one(65535), 0)

    def test_tsn_gt(self):
        self.assertFalse(tsn_gt(0, 1))
        self.assertFalse(tsn_gt(1, 1))
        self.assertTrue(tsn_gt(2, 1))
        self.assertTrue(tsn_gt(2147483648, 1))
        self.assertFalse(tsn_gt(2147483649, 1))
        self.assertFalse(tsn_gt(4294967295, 1))

    def test_tsn_gte(self):
        self.assertFalse(tsn_gte(0, 1))
        self.assertTrue(tsn_gte(1, 1))
        self.assertTrue(tsn_gte(2, 1))
        self.assertTrue(tsn_gte(2147483648, 1))
        self.assertFalse(tsn_gte(2147483649, 1))
        self.assertFalse(tsn_gte(4294967295, 1))


class RTCSctpTransportTest(TestCase):
    def test_construct(self):
        dtlsTransport, _ = dummy_dtls_transport_pair()
        sctpTransport = RTCSctpTransport(dtlsTransport)
        self.assertEqual(sctpTransport.transport, dtlsTransport)
        self.assertEqual(sctpTransport.port, 5000)

    def test_construct_invalid_dtls_transport_state(self):
        dtlsTransport = DummyDtlsTransport(state='closed')
        with self.assertRaises(InvalidStateError):
            RTCSctpTransport(dtlsTransport)

    def test_connect_broken_transport(self):
        """
        Transport with 100% loss never connects.
        """
        client_transport, server_transport = dummy_dtls_transport_pair(loss=1)
        client = RTCSctpTransport(client_transport)
        client._rto = 0.1
        self.assertFalse(client.is_server)
        server = RTCSctpTransport(server_transport)
        server._rto = 0.1
        self.assertTrue(server.is_server)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_connect_lossy_transport(self):
        """
        Transport with 40% loss eventually connects.
        """
        client_transport, server_transport = dummy_dtls_transport_pair(loss=0.4)
        client = RTCSctpTransport(client_transport)
        client._rto = 0.1
        self.assertFalse(client.is_server)
        server = RTCSctpTransport(server_transport)
        server._rto = 0.1
        self.assertTrue(server.is_server)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)

        # transmit data
        server_queue = asyncio.Queue()

        async def server_fake_receive(*args):
            await server_queue.put(args)

        server._receive = server_fake_receive

        for i in range(20):
            message = (123, i, b'ping')
            run(client._send(*message))
            received = run(server_queue.get())
            self.assertEqual(received, message)

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_connect_then_client_creates_data_channel(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        self.assertFalse(client.is_server)
        server = RTCSctpTransport(server_transport)
        self.assertTrue(server.is_server)

        client_channels = track_channels(client)
        server_channels = track_channels(server)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)

        # create data channel
        channel = RTCDataChannel(client, RTCDataChannelParameters(label='chat'))
        self.assertEqual(channel.id, None)
        self.assertEqual(channel.label, 'chat')

        run(asyncio.sleep(0.5))
        self.assertEqual(channel.id, 1)
        self.assertEqual(channel.label, 'chat')
        self.assertEqual(len(client_channels), 0)
        self.assertEqual(len(server_channels), 1)
        self.assertEqual(server_channels[0].id, 1)
        self.assertEqual(server_channels[0].label, 'chat')

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_connect_then_server_creates_data_channel(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        self.assertFalse(client.is_server)
        server = RTCSctpTransport(server_transport)
        self.assertTrue(server.is_server)

        client_channels = track_channels(client)
        server_channels = track_channels(server)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)

        # create data channel
        channel = RTCDataChannel(server, RTCDataChannelParameters(label='chat'))
        self.assertEqual(channel.id, None)
        self.assertEqual(channel.label, 'chat')

        run(asyncio.sleep(0.5))
        self.assertEqual(len(client_channels), 1)
        self.assertEqual(client_channels[0].id, 0)
        self.assertEqual(client_channels[0].label, 'chat')
        self.assertEqual(len(server_channels), 0)

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_abrupt_disconnect(self):
        client_transport, server_transport = dummy_dtls_transport_pair()

        client = RTCSctpTransport(client_transport)
        server = RTCSctpTransport(server_transport)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)

        # break one connection
        run(client_transport.close())
        run(asyncio.sleep(0.1))
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)

        # break other connection
        run(server_transport.close())
        run(asyncio.sleep(0.1))
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

        # try closing again
        run(client.stop())
        run(server.stop())

    def test_abort(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        server = RTCSctpTransport(server_transport)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)

        # shutdown
        run(client._abort())
        run(asyncio.sleep(0.5))
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_garbage(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        server = RTCSctpTransport(server_transport)
        server.start(RTCSctpCapabilities(maxMessageSize=65536), 5000)
        asyncio.ensure_future(client_transport.send(b'garbage'))

        # check outcome
        run(asyncio.sleep(0.5))
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

        # shutdown
        run(server.stop())

    def test_bad_verification_tag(self):
        # verification tag is 12345 instead of 0
        data = load('sctp_init_bad_verification.bin')

        client_transport, server_transport = dummy_dtls_transport_pair()
        server = RTCSctpTransport(server_transport)
        server.start(RTCSctpCapabilities(maxMessageSize=65536), 5000)
        asyncio.ensure_future(client_transport.send(data))

        # check outcome
        run(asyncio.sleep(0.5))
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

        # shutdown
        run(server.stop())

    def test_bad_cookie(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        server = RTCSctpTransport(server_transport)

        # corrupt cookie
        real_send_chunk = client._send_chunk

        async def mock_send_chunk(chunk):
            if isinstance(chunk, CookieEchoChunk):
                chunk.body = b'garbage'
            return await real_send_chunk(chunk)

        client._send_chunk = mock_send_chunk

        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(asyncio.sleep(0.5))
        self.assertEqual(client.state, RTCSctpTransport.State.COOKIE_ECHOED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_stale_cookie(self):
        def mock_timestamp():
            mock_timestamp.calls += 1
            if mock_timestamp.calls == 1:
                return 0
            else:
                return 61

        mock_timestamp.calls = 0

        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        server = RTCSctpTransport(server_transport)

        server._get_timestamp = mock_timestamp
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(asyncio.sleep(0.5))
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_receive_data(self):
        client_transport, _ = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 0

        # receive chunk
        chunk = DataChunk(flags=(SCTP_DATA_FIRST_FRAG | SCTP_DATA_LAST_FRAG))
        chunk.user_data = b'foo'
        chunk.tsn = 1
        run(client._receive_chunk(chunk))

        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
        self.assertEqual(client._last_received_tsn, 1)
        client._sack_needed = False

        # receive chunk again
        run(client._receive_chunk(chunk))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [1])
        self.assertEqual(client._last_received_tsn, 1)

    def test_receive_data_out_of_order(self):
        client_transport, _ = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 0

        # build chunks
        chunks = []

        chunk = DataChunk(flags=SCTP_DATA_FIRST_FRAG)
        chunk.user_data = b'foo'
        chunk.tsn = 1
        chunks.append(chunk)

        chunk = DataChunk()
        chunk.user_data = b'bar'
        chunk.tsn = 2
        chunks.append(chunk)

        chunk = DataChunk(flags=SCTP_DATA_LAST_FRAG)
        chunk.user_data = b'baz'
        chunk.tsn = 3
        chunks.append(chunk)

        # receive first chunk
        run(client._receive_chunk(chunks[0]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
        self.assertEqual(client._sack_misordered, set())
        self.assertEqual(client._last_received_tsn, 1)
        client._sack_needed = False

        # receive last chunk
        run(client._receive_chunk(chunks[2]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
        self.assertEqual(client._sack_misordered, set([3]))
        self.assertEqual(client._last_received_tsn, 1)
        client._sack_needed = False

        # receive middle chunk
        run(client._receive_chunk(chunks[1]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
        self.assertEqual(client._sack_misordered, set([]))
        self.assertEqual(client._last_received_tsn, 3)
        client._sack_needed = False

        # receive last chunk again
        run(client._receive_chunk(chunks[2]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [3])
        self.assertEqual(client._sack_misordered, set([]))
        self.assertEqual(client._last_received_tsn, 3)
        client._sack_needed = False

    def test_receive_heartbeat(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 0
        client._remote_port = 5000

        # receive heartbeat
        chunk = HeartbeatChunk()
        chunk.params.append((1, b'\x01\x02\x03\x04'))
        chunk.tsn = 1
        run(client._receive_chunk(chunk))

        # check response
        data = run(server_transport.recv())
        packet = Packet.parse(data)
        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], HeartbeatAckChunk))
        self.assertEqual(packet.chunks[0].params, [(1, b'\x01\x02\x03\x04')])

    def test_receive_sack_discard(self):
        client_transport, _ = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 0

        # receive sack
        sack_point = client._last_sacked_tsn
        chunk = SackChunk()
        chunk.cumulative_tsn = tsn_minus_one(sack_point)
        run(client._receive_chunk(chunk))

        # sack point must not changed
        self.assertEqual(client._last_sacked_tsn, sack_point)

    def test_mark_received(self):
        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
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

    def test_send_sack(self):
        sack = None

        async def mock_send_chunk(c):
            nonlocal sack
            sack = c

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 123
        client._send_chunk = mock_send_chunk

        run(client._send_sack())
        self.assertIsNotNone(sack)
        self.assertEqual(sack.duplicates, [])
        self.assertEqual(sack.gaps, [])
        self.assertEqual(sack.cumulative_tsn, 123)

    def test_send_sack_with_duplicates(self):
        sack = None

        async def mock_send_chunk(c):
            nonlocal sack
            sack = c

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 123
        client._sack_duplicates = [125, 127]
        client._send_chunk = mock_send_chunk

        run(client._send_sack())
        self.assertIsNotNone(sack)
        self.assertEqual(sack.duplicates, [125, 127])
        self.assertEqual(sack.gaps, [])
        self.assertEqual(sack.cumulative_tsn, 123)

    def test_send_sack_with_gaps(self):
        sack = None

        async def mock_send_chunk(c):
            nonlocal sack
            sack = c

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 12
        client._sack_misordered = [14, 15, 17]
        client._send_chunk = mock_send_chunk

        run(client._send_sack())
        self.assertIsNotNone(sack)
        self.assertEqual(sack.duplicates, [])
        self.assertEqual(sack.gaps, [(2, 3), (5, 5)])
        self.assertEqual(sack.cumulative_tsn, 12)
