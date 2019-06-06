import asyncio
import binascii
import contextlib
import io
import random
from unittest import TestCase

from aioquic import tls
from aioquic.buffer import Buffer, push_bytes, push_uint_var
from aioquic.connection import (
    QuicConnection,
    QuicConnectionError,
    QuicNetworkPath,
    QuicReceiveContext,
)
from aioquic.crypto import CryptoPair
from aioquic.packet import (
    PACKET_TYPE_INITIAL,
    QuicErrorCode,
    QuicFrameType,
    QuicProtocolVersion,
    encode_quic_retry,
    encode_quic_version_negotiation,
)
from aioquic.packet_builder import QuicPacketBuilder

from .utils import SERVER_CERTIFICATE, SERVER_PRIVATE_KEY, run

CLIENT_ADDR = ("1.2.3.4", 1234)

SERVER_ADDR = ("2.3.4.5", 4433)


def encode_uint_var(v):
    buf = Buffer(capacity=8)
    push_uint_var(buf, v)
    return buf.data


class FakeTransport:
    sent = 0
    target = None

    def __init__(self, local_addr, loss=0):
        self.local_addr = local_addr
        self.loss = loss

    def sendto(self, data, addr):
        self.sent += 1
        if self.target is not None and random.random() >= self.loss:
            self.target.datagram_received(data, self.local_addr)


def client_receive_context(client, epoch=tls.Epoch.ONE_RTT):
    return QuicReceiveContext(
        epoch=epoch, host_cid=client.host_cid, network_path=client._network_paths[0]
    )


def create_standalone_client():
    client = QuicConnection(is_client=True)
    client_transport = FakeTransport(CLIENT_ADDR, loss=0)
    client.connection_made(client_transport)

    # like connect() but without waiting
    client._network_paths = [QuicNetworkPath(SERVER_ADDR, is_validated=True)]
    client._version = max(client.supported_versions)
    client._connect()

    return client, client_transport


def create_transport(client, server, loss=0):
    client_transport = FakeTransport(CLIENT_ADDR, loss=loss)
    client_transport.target = server

    server_transport = FakeTransport(SERVER_ADDR, loss=loss)
    server_transport.target = client

    server.connection_made(server_transport)
    client.connection_made(client_transport)

    # like connect() but without waiting
    client._network_paths = [QuicNetworkPath(SERVER_ADDR, is_validated=True)]
    client._version = max(client.supported_versions)
    client._connect()

    return client_transport, server_transport


@contextlib.contextmanager
def client_and_server(
    client_options={},
    client_patch=lambda x: None,
    server_options={},
    server_patch=lambda x: None,
    transport_options={},
):
    client = QuicConnection(is_client=True, **client_options)
    client_patch(client)

    server = QuicConnection(
        is_client=False,
        certificate=SERVER_CERTIFICATE,
        private_key=SERVER_PRIVATE_KEY,
        **server_options
    )
    server_patch(server)

    # perform handshake
    create_transport(client, server, **transport_options)

    yield client, server

    # close
    client.close()
    server.close()


def sequence_numbers(connection_ids):
    return list(map(lambda x: x.sequence_number, connection_ids))


class QuicConnectionTest(TestCase):
    def _test_connect_with_version(self, client_versions, server_versions):
        with client_and_server(
            client_options={"supported_versions": client_versions},
            server_options={"supported_versions": server_versions},
        ) as (client, server):
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # check each endpoint has available connection IDs for the peer
            self.assertEqual(
                sequence_numbers(client._peer_cid_available), [1, 2, 3, 4, 5, 6, 7]
            )
            self.assertEqual(
                sequence_numbers(server._peer_cid_available), [1, 2, 3, 4, 5, 6, 7]
            )

            # send data over stream
            client_reader, client_writer = run(client.create_stream())
            client_writer.write(b"ping")
            run(asyncio.sleep(0))
            self.assertEqual(client._transport.sent, 5)
            self.assertEqual(server._transport.sent, 4)

            # FIXME: needs an API
            server_reader, server_writer = (
                server.streams[0].reader,
                server.streams[0].writer,
            )
            self.assertEqual(run(server_reader.read(1024)), b"ping")
            server_writer.write(b"pong")
            run(asyncio.sleep(0))
            self.assertEqual(client._transport.sent, 6)
            self.assertEqual(server._transport.sent, 5)

            # client receives pong
            self.assertEqual(run(client_reader.read(1024)), b"pong")

            # client writes EOF
            client_writer.write_eof()
            run(asyncio.sleep(0))
            self.assertEqual(client._transport.sent, 7)
            self.assertEqual(server._transport.sent, 6)

            # server receives EOF
            self.assertEqual(run(server_reader.read()), b"")

    def test_connect_draft_19(self):
        self._test_connect_with_version(
            client_versions=[QuicProtocolVersion.DRAFT_19],
            server_versions=[QuicProtocolVersion.DRAFT_19],
        )

    def test_connect_draft_20(self):
        self._test_connect_with_version(
            client_versions=[QuicProtocolVersion.DRAFT_20],
            server_versions=[QuicProtocolVersion.DRAFT_20],
        )

    def test_connect_with_log(self):
        client_log_file = io.StringIO()
        server_log_file = io.StringIO()
        with client_and_server(
            client_options={"secrets_log_file": client_log_file},
            server_options={"secrets_log_file": server_log_file},
        ) as (client, server):
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # check secrets were logged
            client_log = client_log_file.getvalue()
            server_log = server_log_file.getvalue()
            self.assertEqual(client_log, server_log)
            labels = []
            for line in client_log.splitlines():
                labels.append(line.split()[0])
            self.assertEqual(
                labels,
                [
                    "QUIC_SERVER_HANDSHAKE_TRAFFIC_SECRET",
                    "QUIC_CLIENT_HANDSHAKE_TRAFFIC_SECRET",
                    "QUIC_SERVER_TRAFFIC_SECRET_0",
                    "QUIC_CLIENT_TRAFFIC_SECRET_0",
                ],
            )

    def test_connection_lost(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # send data over stream
            client_reader, client_writer = run(client.create_stream())
            client_writer.write(b"ping")
            run(asyncio.sleep(0))
            self.assertEqual(client._transport.sent, 5)
            self.assertEqual(server._transport.sent, 4)

            # break connection
            client.connection_lost(None)
            self.assertEqual(run(client_reader.read()), b"")

    def test_connection_lost_with_exception(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # send data over stream
            client_reader, client_writer = run(client.create_stream())
            client_writer.write(b"ping")
            run(asyncio.sleep(0))
            self.assertEqual(client._transport.sent, 5)
            self.assertEqual(server._transport.sent, 4)

            # break connection
            exc = Exception("some error")
            client.connection_lost(exc)
            with self.assertRaises(Exception) as cm:
                run(client_reader.read())
            self.assertEqual(cm.exception, exc)

    def test_consume_connection_id(self):
        with client_and_server() as (client, server):
            self.assertEqual(
                sequence_numbers(client._peer_cid_available), [1, 2, 3, 4, 5, 6, 7]
            )

            # change connection ID
            client._consume_connection_id()
            self.assertEqual(
                sequence_numbers(client._peer_cid_available), [2, 3, 4, 5, 6, 7]
            )

            # the server provides a new connection ID
            client._send_pending()
            self.assertEqual(
                sequence_numbers(client._peer_cid_available), [2, 3, 4, 5, 6, 7, 8]
            )

    def test_create_stream(self):
        with client_and_server() as (client, server):
            # client
            reader, writer = run(client.create_stream())
            self.assertEqual(writer.get_extra_info("stream_id"), 0)
            self.assertIsNotNone(writer.get_extra_info("connection"))

            reader, writer = run(client.create_stream())
            self.assertEqual(writer.get_extra_info("stream_id"), 4)

            reader, writer = run(client.create_stream(is_unidirectional=True))
            self.assertEqual(writer.get_extra_info("stream_id"), 2)

            reader, writer = run(client.create_stream(is_unidirectional=True))
            self.assertEqual(writer.get_extra_info("stream_id"), 6)

            # server
            reader, writer = run(server.create_stream())
            self.assertEqual(writer.get_extra_info("stream_id"), 1)

            reader, writer = run(server.create_stream())
            self.assertEqual(writer.get_extra_info("stream_id"), 5)

            reader, writer = run(server.create_stream(is_unidirectional=True))
            self.assertEqual(writer.get_extra_info("stream_id"), 3)

            reader, writer = run(server.create_stream(is_unidirectional=True))
            self.assertEqual(writer.get_extra_info("stream_id"), 7)

    def test_create_stream_over_max_streams(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # create streams
            for i in range(128):
                client_reader, client_writer = run(client.create_stream())
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # create one too many
            with self.assertRaises(ValueError) as cm:
                client_reader, client_writer = run(client.create_stream())
            self.assertEqual(str(cm.exception), "Too many streams open")

    def test_decryption_error(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 3)

            # mess with encryption key
            server.cryptos[tls.Epoch.ONE_RTT].send.setup(
                tls.CipherSuite.AES_128_GCM_SHA256, bytes(48)
            )

            # close
            server.close(error_code=QuicErrorCode.NO_ERROR)
            self.assertEqual(client._transport.sent, 4)
            self.assertEqual(server._transport.sent, 4)

    def test_tls_error(self):
        def patch(client):
            real_initialize = client._initialize

            def patched_initialize(peer_cid: bytes):
                real_initialize(peer_cid)
                client.tls._supported_versions = [tls.TLS_VERSION_1_3_DRAFT_28]

            client._initialize = patched_initialize

        # handshake fails
        with client_and_server(client_patch=patch) as (client, server):
            self.assertEqual(client._transport.sent, 1)
            self.assertEqual(server._transport.sent, 1)

    def test_datagram_received_wrong_version(self):
        client, client_transport = create_standalone_client()
        self.assertEqual(client_transport.sent, 1)

        builder = QuicPacketBuilder(
            host_cid=client.peer_cid,
            peer_cid=client.host_cid,
            version=0xFF000011,  # DRAFT_16
        )
        crypto = CryptoPair()
        crypto.setup_initial(client.host_cid, is_client=False)
        builder.start_packet(PACKET_TYPE_INITIAL, crypto)
        push_bytes(builder.buffer, bytes(1200))
        builder.end_packet()

        for datagram in builder.flush()[0]:
            client.datagram_received(datagram, SERVER_ADDR)
        self.assertEqual(client_transport.sent, 1)

    def test_datagram_received_retry(self):
        client, client_transport = create_standalone_client()
        self.assertEqual(client_transport.sent, 1)

        client.datagram_received(
            encode_quic_retry(
                version=QuicProtocolVersion.DRAFT_20,
                source_cid=binascii.unhexlify("85abb547bf28be97"),
                destination_cid=client.host_cid,
                original_destination_cid=client.peer_cid,
                retry_token=bytes(16),
            ),
            SERVER_ADDR,
        )
        self.assertEqual(client_transport.sent, 2)

    def test_datagram_received_retry_wrong_destination_cid(self):
        client, client_transport = create_standalone_client()
        self.assertEqual(client_transport.sent, 1)

        client.datagram_received(
            encode_quic_retry(
                version=QuicProtocolVersion.DRAFT_20,
                source_cid=binascii.unhexlify("85abb547bf28be97"),
                destination_cid=binascii.unhexlify("c98343fe8f5f0ff4"),
                original_destination_cid=client.peer_cid,
                retry_token=bytes(16),
            ),
            SERVER_ADDR,
        )
        self.assertEqual(client_transport.sent, 1)

    def test_error_received(self):
        client = QuicConnection(is_client=True)
        client.error_received(OSError("foo"))

    def test_handle_ack_frame_ecn(self):
        client, client_transport = create_standalone_client()

        client._handle_ack_frame(
            client_receive_context(client),
            QuicFrameType.ACK_ECN,
            Buffer(data=b"\x00\x02\x00\x00\x00\x00\x00"),
        )

    def test_handle_connection_close_frame(self):
        with client_and_server() as (client, server):
            server.close(
                error_code=QuicErrorCode.NO_ERROR, frame_type=QuicFrameType.PADDING
            )

    def test_handle_connection_close_frame_app(self):
        with client_and_server() as (client, server):
            server.close(error_code=QuicErrorCode.NO_ERROR)

    def test_handle_data_blocked_frame(self):
        with client_and_server() as (client, server):
            # client receives DATA_BLOCKED: 12345
            client._handle_data_blocked_frame(
                client_receive_context(client),
                QuicFrameType.DATA_BLOCKED,
                Buffer(data=encode_uint_var(12345)),
            )

    def test_handle_max_data_frame(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._remote_max_data, 1048576)

            # client receives MAX_DATA raising limit
            client._handle_max_data_frame(
                client_receive_context(client),
                QuicFrameType.MAX_DATA,
                Buffer(data=encode_uint_var(1048577)),
            )
            self.assertEqual(client._remote_max_data, 1048577)

    def test_handle_max_stream_data_frame(self):
        with client_and_server() as (client, server):
            # client creates bidirectional stream 0
            reader, writer = run(client.create_stream())
            stream = writer.transport
            self.assertEqual(stream.max_stream_data_remote, 1048576)

            # client receives MAX_STREAM_DATA raising limit
            client._handle_max_stream_data_frame(
                client_receive_context(client),
                QuicFrameType.MAX_STREAM_DATA,
                Buffer(data=b"\x00" + encode_uint_var(1048577)),
            )
            self.assertEqual(stream.max_stream_data_remote, 1048577)

            # client receives MAX_STREAM_DATA lowering limit
            client._handle_max_stream_data_frame(
                client_receive_context(client),
                QuicFrameType.MAX_STREAM_DATA,
                Buffer(data=b"\x00" + encode_uint_var(1048575)),
            )
            self.assertEqual(stream.max_stream_data_remote, 1048577)

    def test_handle_max_stream_data_frame_receive_only(self):
        with client_and_server() as (client, server):
            # server creates unidirectional stream 3
            run(server.create_stream(is_unidirectional=True))

            # client receives MAX_STREAM_DATA: 3, 1
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_max_stream_data_frame(
                    client_receive_context(client),
                    QuicFrameType.MAX_STREAM_DATA,
                    Buffer(data=b"\x03\x01"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.MAX_STREAM_DATA)
            self.assertEqual(cm.exception.reason_phrase, "Stream is receive-only")

    def test_handle_max_streams_bidi_frame(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._remote_max_streams_bidi, 128)

            # client receives MAX_STREAMS_BIDI raising limit
            client._handle_max_streams_bidi_frame(
                client_receive_context(client),
                QuicFrameType.MAX_STREAMS_BIDI,
                Buffer(data=encode_uint_var(129)),
            )
            self.assertEqual(client._remote_max_streams_bidi, 129)

            # client receives MAX_STREAMS_BIDI lowering limit
            client._handle_max_streams_bidi_frame(
                client_receive_context(client),
                QuicFrameType.MAX_STREAMS_BIDI,
                Buffer(data=encode_uint_var(127)),
            )
            self.assertEqual(client._remote_max_streams_bidi, 129)

    def test_handle_max_streams_uni_frame(self):
        with client_and_server() as (client, server):
            self.assertEqual(client._remote_max_streams_uni, 128)

            # client receives MAX_STREAMS_UNI raising limit
            client._handle_max_streams_uni_frame(
                client_receive_context(client),
                QuicFrameType.MAX_STREAMS_UNI,
                Buffer(data=encode_uint_var(129)),
            )
            self.assertEqual(client._remote_max_streams_uni, 129)

            # client receives MAX_STREAMS_UNI raising limit
            client._handle_max_streams_uni_frame(
                client_receive_context(client),
                QuicFrameType.MAX_STREAMS_UNI,
                Buffer(data=encode_uint_var(127)),
            )
            self.assertEqual(client._remote_max_streams_uni, 129)

    def test_handle_new_token_frame(self):
        with client_and_server() as (client, server):
            # client receives NEW_TOKEN
            client._handle_new_token_frame(
                client_receive_context(client),
                QuicFrameType.NEW_TOKEN,
                Buffer(data=binascii.unhexlify("080102030405060708")),
            )

    def test_handle_path_challenge_frame(self):
        with client_and_server() as (client, server):
            # client changes address and sends some data
            client._transport.local_addr = ("1.2.3.4", 2345)
            reader, writer = run(client.create_stream())
            writer.write(b"01234567")
            run(asyncio.sleep(0))

            # server sends PATH_CHALLENGE and receives PATH_RESPONSE
            self.assertEqual(len(server._network_paths), 2)

            # check new path
            self.assertEqual(server._network_paths[0].addr, ("1.2.3.4", 2345))
            self.assertTrue(server._network_paths[0].is_validated)

            # check old path
            self.assertEqual(server._network_paths[1].addr, ("1.2.3.4", 1234))
            self.assertTrue(server._network_paths[1].is_validated)

    def test_handle_path_response_frame_bad(self):
        with client_and_server() as (client, server):
            # server receives unsollicited PATH_RESPONSE
            with self.assertRaises(QuicConnectionError) as cm:
                server._handle_path_response_frame(
                    client_receive_context(client),
                    QuicFrameType.PATH_RESPONSE,
                    Buffer(data=b"\x11\x22\x33\x44\x55\x66\x77\x88"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.PROTOCOL_VIOLATION)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.PATH_RESPONSE)

    def test_handle_reset_stream_frame(self):
        with client_and_server() as (client, server):
            # client creates bidirectional stream 0
            run(client.create_stream())

            # client receives RESET_STREAM
            client._handle_reset_stream_frame(
                client_receive_context(client),
                QuicFrameType.RESET_STREAM,
                Buffer(data=binascii.unhexlify("00112200")),
            )

    def test_handle_reset_stream_frame_send_only(self):
        with client_and_server() as (client, server):
            # client creates unidirectional stream 2
            run(client.create_stream(is_unidirectional=True))

            # client receives RESET_STREAM
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_reset_stream_frame(
                    client_receive_context(client),
                    QuicFrameType.RESET_STREAM,
                    Buffer(data=binascii.unhexlify("02112200")),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.RESET_STREAM)
            self.assertEqual(cm.exception.reason_phrase, "Stream is send-only")

    def test_handle_retire_connection_id_frame(self):
        with client_and_server() as (client, server):
            self.assertEqual(
                sequence_numbers(client._host_cids), [0, 1, 2, 3, 4, 5, 6, 7]
            )

            # client receives RETIRE_CONNECTION_ID
            client._handle_retire_connection_id_frame(
                client_receive_context(client),
                QuicFrameType.RETIRE_CONNECTION_ID,
                Buffer(data=b"\x02"),
            )
            self.assertEqual(
                sequence_numbers(client._host_cids), [0, 1, 3, 4, 5, 6, 7, 8]
            )

    def test_handle_retire_connection_id_frame_current_cid(self):
        with client_and_server() as (client, server):
            # client receives RETIRE_CONNECTION_ID for the current CID
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_retire_connection_id_frame(
                    client_receive_context(client),
                    QuicFrameType.RETIRE_CONNECTION_ID,
                    Buffer(data=b"\x00"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.PROTOCOL_VIOLATION)
            self.assertEqual(
                cm.exception.frame_type, QuicFrameType.RETIRE_CONNECTION_ID
            )
            self.assertEqual(
                cm.exception.reason_phrase, "Cannot retire current connection ID"
            )
            self.assertEqual(
                sequence_numbers(client._host_cids), [0, 1, 2, 3, 4, 5, 6, 7]
            )

    def test_handle_stop_sending_frame(self):
        with client_and_server() as (client, server):
            # client creates bidirectional stream 0
            run(client.create_stream())

            # client receives STOP_SENDING
            client._handle_stop_sending_frame(
                client_receive_context(client),
                QuicFrameType.STOP_SENDING,
                Buffer(data=b"\x00\x11\x22"),
            )

    def test_handle_stop_sending_frame_receive_only(self):
        with client_and_server() as (client, server):
            # server creates unidirectional stream 3
            run(server.create_stream(is_unidirectional=True))

            # client receives STOP_SENDING
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stop_sending_frame(
                    client_receive_context(client),
                    QuicFrameType.STOP_SENDING,
                    Buffer(data=b"\x03\x11\x22"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.STOP_SENDING)
            self.assertEqual(cm.exception.reason_phrase, "Stream is receive-only")

    def test_handle_stream_frame_over_max_data(self):
        with client_and_server() as (client, server):
            # artificially raise received data counter
            client._local_max_data_used = client._local_max_data

            # client receives STREAM frame
            frame_type = QuicFrameType.STREAM_BASE | 4
            stream_id = 1
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stream_frame(
                    client_receive_context(client),
                    frame_type,
                    Buffer(data=encode_uint_var(stream_id) + encode_uint_var(1)),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.FLOW_CONTROL_ERROR)
            self.assertEqual(cm.exception.frame_type, frame_type)
            self.assertEqual(cm.exception.reason_phrase, "Over connection data limit")

    def test_handle_stream_frame_over_max_stream_data(self):
        with client_and_server() as (client, server):
            # client receives STREAM frame
            frame_type = QuicFrameType.STREAM_BASE | 4
            stream_id = 1
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stream_frame(
                    client_receive_context(client),
                    frame_type,
                    Buffer(
                        data=encode_uint_var(stream_id)
                        + encode_uint_var(client._local_max_stream_data_bidi_remote + 1)
                    ),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.FLOW_CONTROL_ERROR)
            self.assertEqual(cm.exception.frame_type, frame_type)
            self.assertEqual(cm.exception.reason_phrase, "Over stream data limit")

    def test_handle_stream_frame_over_max_streams(self):
        with client_and_server() as (client, server):
            # client receives STREAM frame
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stream_frame(
                    client_receive_context(client),
                    QuicFrameType.STREAM_BASE,
                    Buffer(
                        data=encode_uint_var(client._local_max_stream_data_uni * 4 + 3)
                    ),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_LIMIT_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_BASE)
            self.assertEqual(cm.exception.reason_phrase, "Too many streams open")

    def test_handle_stream_frame_send_only(self):
        with client_and_server() as (client, server):
            # client creates unidirectional stream 2
            run(client.create_stream(is_unidirectional=True))

            # client receives STREAM frame
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stream_frame(
                    client_receive_context(client),
                    QuicFrameType.STREAM_BASE,
                    Buffer(data=b"\x02"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_BASE)
            self.assertEqual(cm.exception.reason_phrase, "Stream is send-only")

    def test_handle_stream_frame_wrong_initiator(self):
        with client_and_server() as (client, server):
            # client receives STREAM frame
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stream_frame(
                    client_receive_context(client),
                    QuicFrameType.STREAM_BASE,
                    Buffer(data=b"\x00"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_BASE)
            self.assertEqual(cm.exception.reason_phrase, "Wrong stream initiator")

    def test_handle_stream_data_blocked_frame(self):
        with client_and_server() as (client, server):
            # client creates bidirectional stream 0
            run(client.create_stream())

            # client receives STREAM_DATA_BLOCKED
            client._handle_stream_data_blocked_frame(
                client_receive_context(client),
                QuicFrameType.STREAM_DATA_BLOCKED,
                Buffer(data=b"\x00\x01"),
            )

    def test_handle_stream_data_blocked_frame_send_only(self):
        with client_and_server() as (client, server):
            # client creates unidirectional stream 2
            run(client.create_stream(is_unidirectional=True))

            # client receives STREAM_DATA_BLOCKED
            with self.assertRaises(QuicConnectionError) as cm:
                client._handle_stream_data_blocked_frame(
                    client_receive_context(client),
                    QuicFrameType.STREAM_DATA_BLOCKED,
                    Buffer(data=b"\x02\x01"),
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_DATA_BLOCKED)
            self.assertEqual(cm.exception.reason_phrase, "Stream is send-only")

    def test_handle_streams_blocked_uni_frame(self):
        with client_and_server() as (client, server):
            # client receives STREAMS_BLOCKED_UNI: 0
            client._handle_streams_blocked_frame(
                client_receive_context(client),
                QuicFrameType.STREAMS_BLOCKED_UNI,
                Buffer(data=b"\x00"),
            )

    def test_payload_received_padding_only(self):
        with client_and_server() as (client, server):
            # client receives padding only
            is_ack_eliciting, is_probing = client._payload_received(
                client_receive_context(client), b"\x00" * 1200
            )
            self.assertFalse(is_ack_eliciting)
            self.assertTrue(is_probing)

    def test_payload_received_unknown_frame(self):
        with client_and_server() as (client, server):
            # client receives unknown frame
            with self.assertRaises(QuicConnectionError) as cm:
                client._payload_received(client_receive_context(client), b"\x1e")
            self.assertEqual(cm.exception.error_code, QuicErrorCode.PROTOCOL_VIOLATION)
            self.assertEqual(cm.exception.frame_type, 0x1E)
            self.assertEqual(cm.exception.reason_phrase, "Unknown frame type")

    def test_payload_received_unexpected_frame(self):
        with client_and_server() as (client, server):
            # client receives CRYPTO frame in 0-RTT
            with self.assertRaises(QuicConnectionError) as cm:
                client._payload_received(
                    client_receive_context(client, epoch=tls.Epoch.ZERO_RTT), b"\x06"
                )
            self.assertEqual(cm.exception.error_code, QuicErrorCode.PROTOCOL_VIOLATION)
            self.assertEqual(cm.exception.frame_type, QuicFrameType.CRYPTO)
            self.assertEqual(cm.exception.reason_phrase, "Unexpected frame type")

    def test_payload_received_malformed_frame(self):
        with client_and_server() as (client, server):
            # client receives malformed frame
            with self.assertRaises(QuicConnectionError) as cm:
                client._payload_received(
                    client_receive_context(client), b"\x1c\x00\x01\x00"
                )
            self.assertEqual(
                cm.exception.error_code, QuicErrorCode.FRAME_ENCODING_ERROR
            )
            self.assertEqual(cm.exception.frame_type, 0x1C)
            self.assertEqual(cm.exception.reason_phrase, "Failed to parse frame")

    def test_stream_direction(self):
        with client_and_server() as (client, server):
            for off in [0, 4, 8]:
                # Client-Initiated, Bidirectional
                self.assertTrue(client._stream_can_receive(off))
                self.assertTrue(client._stream_can_send(off))
                self.assertTrue(server._stream_can_receive(off))
                self.assertTrue(server._stream_can_send(off))

                # Server-Initiated, Bidirectional
                self.assertTrue(client._stream_can_receive(off + 1))
                self.assertTrue(client._stream_can_send(off + 1))
                self.assertTrue(server._stream_can_receive(off + 1))
                self.assertTrue(server._stream_can_send(off + 1))

                # Client-Initiated, Unidirectional
                self.assertFalse(client._stream_can_receive(off + 2))
                self.assertTrue(client._stream_can_send(off + 2))
                self.assertTrue(server._stream_can_receive(off + 2))
                self.assertFalse(server._stream_can_send(off + 2))

                # Server-Initiated, Unidirectional
                self.assertTrue(client._stream_can_receive(off + 3))
                self.assertFalse(client._stream_can_send(off + 3))
                self.assertFalse(server._stream_can_receive(off + 3))
                self.assertTrue(server._stream_can_send(off + 3))

    def test_version_negotiation_fail(self):
        client, client_transport = create_standalone_client()
        self.assertEqual(client_transport.sent, 1)

        # no common version, no retry
        client.datagram_received(
            encode_quic_version_negotiation(
                source_cid=client.peer_cid,
                destination_cid=client.host_cid,
                supported_versions=[0xFF000011],  # DRAFT_16
            ),
            SERVER_ADDR,
        )
        self.assertEqual(client_transport.sent, 1)

    def test_version_negotiation_ok(self):
        client, client_transport = create_standalone_client()
        self.assertEqual(client_transport.sent, 1)

        # found a common version, retry
        client.datagram_received(
            encode_quic_version_negotiation(
                source_cid=client.peer_cid,
                destination_cid=client.host_cid,
                supported_versions=[QuicProtocolVersion.DRAFT_19],
            ),
            SERVER_ADDR,
        )
        self.assertEqual(client_transport.sent, 2)

    def test_with_packet_loss(self):
        with client_and_server() as (client, server):
            # create stream
            client_reader, client_writer = run(client.create_stream())
            client_writer.write(b"ping")
            run(asyncio.sleep(0))
            server_reader = server.streams[0].reader
            self.assertEqual(run(server_reader.read(4)), b"ping")

            # cause packet loss in both directions
            client._transport.loss = 0.2
            server._transport.loss = 0.2

            # send data over stream
            sent_data = b"Z" * 1000000
            client_writer.write(sent_data)
            client_writer.write_eof()

            # wait for the data to be received
            self.assertEqual(run(server_reader.read()), sent_data)


class QuicNetworkPathTest(TestCase):
    def test_can_send(self):
        path = QuicNetworkPath(("1.2.3.4", 1234))
        self.assertFalse(path.is_validated)

        # initially, cannot send any data
        self.assertTrue(path.can_send(0))
        self.assertFalse(path.can_send(1))

        # receive some data
        path.bytes_received += 1
        self.assertTrue(path.can_send(0))
        self.assertTrue(path.can_send(1))
        self.assertTrue(path.can_send(2))
        self.assertTrue(path.can_send(3))
        self.assertFalse(path.can_send(4))

        # send some data
        path.bytes_sent += 3
        self.assertTrue(path.can_send(0))
        self.assertFalse(path.can_send(1))
