import asyncio
from unittest import TestCase

from aiortc.exceptions import InvalidStateError
from aiortc.rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from aiortc.rtcsctptransport import (SCTP_DATA_FIRST_FRAG, SCTP_DATA_LAST_FRAG,
                                     USERDATA_MAX_LENGTH, AbortChunk,
                                     CookieEchoChunk, DataChunk, ErrorChunk,
                                     HeartbeatAckChunk, HeartbeatChunk,
                                     InboundStream, InitChunk, Packet,
                                     ReconfigChunk, RTCSctpCapabilities,
                                     RTCSctpTransport, SackChunk,
                                     ShutdownAckChunk, ShutdownChunk,
                                     ShutdownCompleteChunk,
                                     StreamAddOutgoingParam,
                                     StreamResetOutgoingParam,
                                     StreamResetResponseParam, seq_gt,
                                     seq_plus_one, tsn_gt, tsn_gte,
                                     tsn_minus_one, tsn_plus_one)

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


class DummyIceTransport:
    role = 'controlling'


class DummyDtlsTransport:
    def __init__(self, state='new'):
        self.state = state
        self.transport = DummyIceTransport()


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
        self.assertEqual(repr(packet.chunks[0]), 'InitChunk(flags=0)')

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

    def test_parse_data(self):
        data = load('sctp_data.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 264304801)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], DataChunk))
        self.assertEqual(packet.chunks[0].type, 0)
        self.assertEqual(packet.chunks[0].flags, 3)
        self.assertEqual(packet.chunks[0].tsn, 2584679421)
        self.assertEqual(packet.chunks[0].stream_id, 1)
        self.assertEqual(packet.chunks[0].stream_seq, 1)
        self.assertEqual(packet.chunks[0].protocol, 51)
        self.assertEqual(packet.chunks[0].user_data, b'ping')
        self.assertEqual(repr(packet.chunks[0]),
                         'DataChunk(flags=3, tsn=2584679421, stream_id=1, stream_seq=1)')

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

    def test_parse_reconfig_reset_out(self):
        data = load('sctp_reconfig_reset_out.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 3370675819)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], ReconfigChunk))
        self.assertEqual(packet.chunks[0].type, 130)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].params, [
            (13, b'\x8b\xd8\n[\xe4\x8b\xecs\x8b\xd8\n^\x00\x01')
        ])

        # Outgoing SSN Reset Request Parameter
        param_data = packet.chunks[0].params[0][1]
        param = StreamResetOutgoingParam.parse(param_data)
        self.assertEqual(param.request_sequence, 2346191451)
        self.assertEqual(param.response_sequence, 3834375283)
        self.assertEqual(param.last_tsn, 2346191454)
        self.assertEqual(param.streams, [1])
        self.assertEqual(bytes(param), param_data)

        self.assertEqual(bytes(packet), data)

    def test_parse_reconfig_add_out(self):
        data = load('sctp_reconfig_add_out.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 3909981950)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], ReconfigChunk))
        self.assertEqual(packet.chunks[0].type, 130)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].params, [
            (17, b'\xca\x02\xf60\x00\x10\x00\x00')
        ])

        # Add Outgoing Streams Request Parameter
        param_data = packet.chunks[0].params[0][1]
        param = StreamAddOutgoingParam.parse(param_data)
        self.assertEqual(param.request_sequence, 3389191728)
        self.assertEqual(param.new_streams, 16)
        self.assertEqual(bytes(param), param_data)

        self.assertEqual(bytes(packet), data)

    def test_parse_reconfig_response(self):
        data = load('sctp_reconfig_response.bin')
        packet = Packet.parse(data)
        self.assertEqual(packet.source_port, 5000)
        self.assertEqual(packet.destination_port, 5000)
        self.assertEqual(packet.verification_tag, 2982117117)

        self.assertEqual(len(packet.chunks), 1)
        self.assertTrue(isinstance(packet.chunks[0], ReconfigChunk))
        self.assertEqual(packet.chunks[0].type, 130)
        self.assertEqual(packet.chunks[0].flags, 0)
        self.assertEqual(packet.chunks[0].params, [
            (16, b'\x91S\x1fT\x00\x00\x00\x01')
        ])

        # Re-configuration Response Parameter
        param_data = packet.chunks[0].params[0][1]
        param = StreamResetResponseParam.parse(param_data)
        self.assertEqual(param.response_sequence, 2438143828)
        self.assertEqual(param.result, 1)
        self.assertEqual(bytes(param), param_data)

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
        self.assertEqual(repr(packet.chunks[0]),
                         'SackChunk(flags=0, advertised_rwnd=128160, cumulative_tsn=2222939037, '
                         'gaps=[(2, 2), (4, 4)])')

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
        with self.assertRaises(AssertionError) as cm:
            stream.add_chunk(self.fragmented[0])
        self.assertEqual(str(cm.exception), '')

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

    def test_tsn_minus_one(self):
        self.assertEqual(tsn_minus_one(0), 4294967295)
        self.assertEqual(tsn_minus_one(1), 0)
        self.assertEqual(tsn_minus_one(4294967294), 4294967293)
        self.assertEqual(tsn_minus_one(4294967295), 4294967294)

    def test_tsn_plus_one(self):
        self.assertEqual(tsn_plus_one(0), 1)
        self.assertEqual(tsn_plus_one(1), 2)
        self.assertEqual(tsn_plus_one(4294967294), 4294967295)
        self.assertEqual(tsn_plus_one(4294967295), 0)


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
        client_transport, server_transport = dummy_dtls_transport_pair(loss=[True])
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
        Transport with 25% loss eventually connects.
        """
        client_transport, server_transport = dummy_dtls_transport_pair(
            loss=[True, False, False, False])

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

    def test_connect_client_limits_streams(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        client.inbound_streams_max = 2048
        client.outbound_streams = 256
        self.assertFalse(client.is_server)
        server = RTCSctpTransport(server_transport)
        self.assertTrue(server.is_server)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(client.inbound_streams, 2048)
        self.assertEqual(client.outbound_streams, 256)
        self.assertEqual(client._remote_extensions, [130])
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.inbound_streams, 256)
        self.assertEqual(server.outbound_streams, 2048)
        self.assertEqual(server._remote_extensions, [130])

        # client requests additional outbound streams
        param = StreamAddOutgoingParam(
            request_sequence=client._reconfig_request_seq,
            new_streams=16)
        run(client._send_reconfig_param(param))

        run(asyncio.sleep(0.5))

        self.assertEqual(server.inbound_streams, 272)
        self.assertEqual(server.outbound_streams, 2048)

        # shutdown
        run(client.stop())
        run(server.stop())
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)
        self.assertEqual(server.state, RTCSctpTransport.State.CLOSED)

    def test_connect_server_limits_streams(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        self.assertFalse(client.is_server)
        server = RTCSctpTransport(server_transport)
        server.inbound_streams_max = 2048
        server.outbound_streams = 256
        self.assertTrue(server.is_server)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(wait_for_outcome(client, server))
        self.assertEqual(client.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(client.inbound_streams, 256)
        self.assertEqual(client.outbound_streams, 2048)
        self.assertEqual(client._remote_extensions, [130])
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.inbound_streams, 2048)
        self.assertEqual(server.outbound_streams, 256)
        self.assertEqual(server._remote_extensions, [130])

        run(asyncio.sleep(0.5))

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
        self.assertEqual(client.inbound_streams, 65535)
        self.assertEqual(client.outbound_streams, 65535)
        self.assertEqual(client._remote_extensions, [130])
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server.inbound_streams, 65535)
        self.assertEqual(server.outbound_streams, 65535)
        self.assertEqual(server._remote_extensions, [130])

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
        self.assertEqual(client._remote_extensions, [130])
        self.assertEqual(server.state, RTCSctpTransport.State.ESTABLISHED)
        self.assertEqual(server._remote_extensions, [130])

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
        """
        Abrupt disconnect causes the __run() loop to exit.
        """
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

    def test_abrupt_disconnect_2(self):
        """
        Abrupt disconnect causes sending ABORT chunk to fail.
        """
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

        # break connection
        run(client_transport.close())
        run(server_transport.close())

        # stop
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

    def test_receive_shutdown(self):
        async def mock_send_chunk(chunk):
            pass

        client_transport, _ = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 0
        client._send_chunk = mock_send_chunk
        client.state = RTCSctpTransport.State.ESTABLISHED

        # receive shutdown
        chunk = ShutdownChunk()
        chunk.cumulative_tsn = tsn_minus_one(client._last_sacked_tsn)
        run(client._receive_chunk(chunk))
        self.assertEqual(client.state, RTCSctpTransport.State.SHUTDOWN_ACK_SENT)

        # receive shutdown complete
        chunk = ShutdownCompleteChunk()
        run(client._receive_chunk(chunk))
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)

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

    def test_send_data(self):
        async def mock_send_chunk(chunk):
            pass

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._send_chunk = mock_send_chunk

        # no data
        run(client._transmit())
        self.assertIsNone(client._t3_handle)
        self.assertEqual(client._outbound_queue_pos, 0)

        # 1 chunk
        run(client._send(123, 456, b'M' * USERDATA_MAX_LENGTH))
        self.assertIsNotNone(client._t3_handle)
        self.assertEqual(len(client._outbound_queue), 1)
        self.assertEqual(client._outbound_queue_pos, 1)

    def test_send_data_over_cwnd(self):
        async def mock_send_chunk(chunk):
            pass

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._send_chunk = mock_send_chunk
        client._ssthresh = 131072

        # STEP 1 - queue 4 chunks, but cwnd only allows 3
        run(client._send(123, 456, b'M' * USERDATA_MAX_LENGTH * 4))

        # T3 timer was started
        self.assertIsNotNone(client._t3_handle)

        self.assertEqual(len(client._outbound_queue), 4)
        self.assertEqual(client._outbound_queue_pos, 3)

        # STEP 2 - sack comes in acknowledging 2 chunks
        previous_timer = client._t3_handle
        sack = SackChunk()
        sack.cumulative_tsn = client._outbound_queue[1].tsn
        run(client._receive_chunk(sack))

        # T3 timer was restarted
        self.assertIsNotNone(client._t3_handle)
        self.assertNotEqual(client._t3_handle, previous_timer)

        self.assertEqual(len(client._outbound_queue), 2)
        self.assertEqual(client._outbound_queue_pos, 2)

        # STEP 3 - sack comes in acknowledging 2 more chunks
        sack = SackChunk()
        sack.cumulative_tsn = client._outbound_queue[1].tsn
        run(client._receive_chunk(sack))

        # T3 timer was stopped
        self.assertIsNone(client._t3_handle)

        self.assertEqual(len(client._outbound_queue), 0)
        self.assertEqual(client._outbound_queue_pos, 0)

    def test_t2_expired_when_shutdown_ack_sent(self):
        async def mock_send_chunk(chunk):
            pass

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._last_received_tsn = 0
        client._send_chunk = mock_send_chunk

        chunk = ShutdownAckChunk()

        # fails once
        client.state = RTCSctpTransport.State.SHUTDOWN_ACK_SENT
        client._t2_start(chunk)
        client._t2_expired()
        self.assertEqual(client._t2_failures, 1)
        self.assertIsNotNone(client._t2_handle)
        self.assertEqual(client.state, RTCSctpTransport.State.SHUTDOWN_ACK_SENT)

        # fails 10 times
        client._t2_failures = 9
        client._t2_expired()
        self.assertEqual(client._t2_failures, 10)
        self.assertIsNotNone(client._t2_handle)
        self.assertEqual(client.state, RTCSctpTransport.State.SHUTDOWN_ACK_SENT)

        # fails 11 times
        client._t2_expired()
        self.assertEqual(client._t2_failures, 11)
        self.assertIsNone(client._t2_handle)
        self.assertEqual(client.state, RTCSctpTransport.State.CLOSED)

        # let async code complete
        run(asyncio.sleep(0))

    def test_t3_expired(self):
        async def mock_send_chunk(chunk):
            pass

        async def mock_transmit():
            pass

        client_transport = DummyDtlsTransport()
        client = RTCSctpTransport(client_transport)
        client._send_chunk = mock_send_chunk

        # 1 chunk
        run(client._send(123, 456, b'M' * USERDATA_MAX_LENGTH))
        self.assertIsNotNone(client._t3_handle)
        self.assertEqual(len(client._outbound_queue), 1)
        self.assertEqual(client._outbound_queue_pos, 1)

        # t3 expires
        client._transmit = mock_transmit
        client._t3_expired()
        self.assertIsNone(client._t3_handle)
        self.assertEqual(len(client._outbound_queue), 1)
        self.assertEqual(client._outbound_queue_pos, 0)

        # let async code complete
        run(asyncio.sleep(0))
