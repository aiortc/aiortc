import asyncio
from unittest import TestCase

from aiortc.exceptions import InvalidStateError
from aiortc.rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from aiortc.rtcsctptransport import (SCTP_DATA_FIRST_FRAG, SCTP_DATA_LAST_FRAG,
                                     AbortChunk, CookieEchoChunk, DataChunk,
                                     ErrorChunk, HeartbeatAckChunk,
                                     HeartbeatChunk, InitChunk, Packet,
                                     RTCSctpCapabilities, RTCSctpTransport,
                                     SackChunk, ShutdownChunk, tsn_gt, tsn_gte)

from .utils import dummy_dtls_transport_pair, load, run


def track_channels(transport):
        channels = []

        @transport.on('datachannel')
        def on_datachannel(channel):
            channels.append(channel)

        return channels


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


class SctpUtilTest(TestCase):
    def test_tsn_gt(self):
        self.assertFalse(tsn_gt(0, 1))
        self.assertFalse(tsn_gt(1, 1))
        self.assertTrue(tsn_gt(2, 1))
        self.assertTrue(tsn_gt(2147483648, 1))
        self.assertFalse(tsn_gt(2147483649, 1))
        self.assertFalse(tsn_gt(4294967296, 1))

    def test_tsn_gte(self):
        self.assertFalse(tsn_gte(0, 1))
        self.assertTrue(tsn_gte(1, 1))
        self.assertTrue(tsn_gte(2, 1))
        self.assertTrue(tsn_gte(2147483648, 1))
        self.assertFalse(tsn_gte(2147483649, 1))
        self.assertFalse(tsn_gte(4294967296, 1))


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
        run(asyncio.sleep(0.5))
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
        run(asyncio.sleep(0.5))
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

    def test_abort(self):
        client_transport, server_transport = dummy_dtls_transport_pair()
        client = RTCSctpTransport(client_transport)
        server = RTCSctpTransport(server_transport)

        # connect
        server.start(client.getCapabilities(), client.port)
        client.start(server.getCapabilities(), server.port)

        # check outcome
        run(asyncio.sleep(0.5))
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
        self.assertEqual(client._last_received_tsn, 1)
        client._sack_needed = False

        # receive last chunk
        run(client._receive_chunk(chunks[2]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
        self.assertEqual(client._last_received_tsn, 1)
        client._sack_needed = False

        # receive middle chunk
        run(client._receive_chunk(chunks[1]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
        self.assertEqual(client._last_received_tsn, 2)
        client._sack_needed = False

        # receive last chunk again
        run(client._receive_chunk(chunks[2]))
        self.assertEqual(client._sack_needed, True)
        self.assertEqual(client._sack_duplicates, [])
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
