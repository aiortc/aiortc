import asyncio
import binascii
import io
from unittest import TestCase

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from aioquic import tls
from aioquic.buffer import Buffer
from aioquic.connection import QuicConnection, QuicConnectionError
from aioquic.packet import (
    QuicErrorCode,
    QuicFrameType,
    QuicProtocolVersion,
    QuicStreamFlag,
    push_uint_var,
)

from .utils import load, run

SERVER_CERTIFICATE = x509.load_pem_x509_certificate(
    load("ssl_cert.pem"), backend=default_backend()
)
SERVER_PRIVATE_KEY = serialization.load_pem_private_key(
    load("ssl_key.pem"), password=None, backend=default_backend()
)


def encode_uint_var(v):
    buf = Buffer(capacity=8)
    push_uint_var(buf, v)
    return buf.data


class FakeTransport:
    sent = 0
    target = None

    def sendto(self, data):
        self.sent += 1
        if self.target is not None:
            self.target.datagram_received(data, None)


def create_transport(client, server):
    client_transport = FakeTransport()
    client_transport.target = server

    server_transport = FakeTransport()
    server_transport.target = client

    server.connection_made(server_transport)
    client.connection_made(client_transport)

    return client_transport, server_transport


class QuicConnectionTest(TestCase):
    def _test_connect_with_version(self, client_versions, server_versions):
        client = QuicConnection(is_client=True)
        client.supported_versions = client_versions
        client.version = max(client_versions)

        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )
        server.supported_versions = server_versions
        server.version = max(server_versions)

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 4)
        run(client.connect())

        # send data over stream
        client_reader, client_writer = client.create_stream()
        client_writer.write(b"ping")
        run(asyncio.sleep(0))
        self.assertEqual(client_transport.sent, 5)
        self.assertEqual(server_transport.sent, 5)

        # FIXME: needs an API
        server_reader, server_writer = (
            server.streams[0].reader,
            server.streams[0].writer,
        )
        self.assertEqual(run(server_reader.read(1024)), b"ping")
        server_writer.write(b"pong")
        run(asyncio.sleep(0))
        self.assertEqual(client_transport.sent, 6)
        self.assertEqual(server_transport.sent, 6)

        # client receives pong
        self.assertEqual(run(client_reader.read(1024)), b"pong")

        # client writes EOF
        client_writer.write_eof()
        run(asyncio.sleep(0))
        self.assertEqual(client_transport.sent, 7)
        self.assertEqual(server_transport.sent, 7)

        # server receives EOF
        self.assertEqual(run(server_reader.read()), b"")

    def test_connect_draft_17(self):
        self._test_connect_with_version(
            client_versions=[QuicProtocolVersion.DRAFT_17],
            server_versions=[QuicProtocolVersion.DRAFT_17],
        )

    def test_connect_draft_18(self):
        self._test_connect_with_version(
            client_versions=[QuicProtocolVersion.DRAFT_18],
            server_versions=[QuicProtocolVersion.DRAFT_18],
        )

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
        client = QuicConnection(is_client=True, secrets_log_file=client_log_file)
        server_log_file = io.StringIO()
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
            secrets_log_file=server_log_file,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 4)

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
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 4)
        run(client.connect())

        # send data over stream
        client_reader, client_writer = client.create_stream()
        client_writer.write(b"ping")
        run(asyncio.sleep(0))
        self.assertEqual(client_transport.sent, 5)
        self.assertEqual(server_transport.sent, 5)

        # break connection
        client.connection_lost(None)
        self.assertEqual(run(client_reader.read()), b"")

    def test_create_stream(self):
        client = QuicConnection(is_client=True)
        client._initialize(b"")

        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )
        server._initialize(b"")

        # client
        reader, writer = client.create_stream()
        self.assertEqual(writer.get_extra_info("stream_id"), 0)
        self.assertIsNotNone(writer.get_extra_info("connection"))

        reader, writer = client.create_stream()
        self.assertEqual(writer.get_extra_info("stream_id"), 4)

        reader, writer = client.create_stream(is_unidirectional=True)
        self.assertEqual(writer.get_extra_info("stream_id"), 2)

        reader, writer = client.create_stream(is_unidirectional=True)
        self.assertEqual(writer.get_extra_info("stream_id"), 6)

        # server
        reader, writer = server.create_stream()
        self.assertEqual(writer.get_extra_info("stream_id"), 1)

        reader, writer = server.create_stream()
        self.assertEqual(writer.get_extra_info("stream_id"), 5)

        reader, writer = server.create_stream(is_unidirectional=True)
        self.assertEqual(writer.get_extra_info("stream_id"), 3)

        reader, writer = server.create_stream(is_unidirectional=True)
        self.assertEqual(writer.get_extra_info("stream_id"), 7)

    def test_decryption_error(self):
        client = QuicConnection(is_client=True)

        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 4)

        # mess with encryption key
        server.spaces[tls.Epoch.ONE_RTT].crypto.send.setup(
            tls.CipherSuite.AES_128_GCM_SHA256, bytes(48)
        )

        # close
        server.close(error_code=QuicErrorCode.NO_ERROR)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 5)

    def test_tls_error(self):
        client = QuicConnection(is_client=True)
        real_initialize = client._initialize

        def patched_initialize(peer_cid: bytes):
            real_initialize(peer_cid)
            client.tls._supported_versions = [tls.TLS_VERSION_1_3_DRAFT_28]

        client._initialize = patched_initialize

        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # fail handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 2)
        self.assertEqual(server_transport.sent, 1)

    def test_error_received(self):
        client = QuicConnection(is_client=True)
        client.error_received(OSError("foo"))

    def test_retry(self):
        client = QuicConnection(is_client=True)
        client.host_cid = binascii.unhexlify("c98343fe8f5f0ff4")
        client.peer_cid = binascii.unhexlify("85abb547bf28be97")

        client_transport = FakeTransport()
        client.connection_made(client_transport)
        self.assertEqual(client_transport.sent, 1)

        client.datagram_received(load("retry.bin"), None)
        self.assertEqual(client_transport.sent, 2)

    def test_handle_ack_frame_ecn(self):
        client = QuicConnection(is_client=True)
        client._handle_ack_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.ACK_ECN,
            Buffer(data=b"\x00\x02\x00\x00\x00\x00\x00"),
        )

    def test_handle_connection_close_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 4)

        # close
        server.close(
            error_code=QuicErrorCode.NO_ERROR, frame_type=QuicFrameType.PADDING
        )
        self.assertEqual(client_transport.sent, 5)
        self.assertEqual(server_transport.sent, 5)

    def test_handle_connection_close_frame_app(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client_transport.sent, 4)
        self.assertEqual(server_transport.sent, 4)

        # close
        server.close(error_code=QuicErrorCode.NO_ERROR)
        self.assertEqual(client_transport.sent, 5)
        self.assertEqual(server_transport.sent, 5)

    def test_handle_data_blocked_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives DATA_BLOCKED: 12345
        client._handle_data_blocked_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.DATA_BLOCKED,
            Buffer(data=encode_uint_var(12345)),
        )

    def test_handle_max_data_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client._remote_max_data, 1048576)

        # client receives MAX_DATA raising limit
        client._handle_max_data_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_DATA,
            Buffer(data=encode_uint_var(1048577)),
        )
        self.assertEqual(client._remote_max_data, 1048577)

    def test_handle_max_stream_data_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates bidirectional stream 0
        stream = client.create_stream()[1].transport
        self.assertEqual(stream.max_stream_data_remote, 1048576)

        # client receives MAX_STREAM_DATA raising limit
        client._handle_max_stream_data_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_STREAM_DATA,
            Buffer(data=b"\x00" + encode_uint_var(1048577)),
        )
        self.assertEqual(stream.max_stream_data_remote, 1048577)

        # client receives MAX_STREAM_DATA lowering limit
        client._handle_max_stream_data_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_STREAM_DATA,
            Buffer(data=b"\x00" + encode_uint_var(1048575)),
        )
        self.assertEqual(stream.max_stream_data_remote, 1048577)

    def test_handle_max_stream_data_frame_receive_only(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # server creates unidirectional stream 3
        server.create_stream(is_unidirectional=True)

        # client receives MAX_STREAM_DATA: 3, 1
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_max_stream_data_frame(
                tls.Epoch.ONE_RTT,
                QuicFrameType.MAX_STREAM_DATA,
                Buffer(data=b"\x03\x01"),
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.MAX_STREAM_DATA)
        self.assertEqual(cm.exception.reason_phrase, "Stream is receive-only")

    def test_handle_max_streams_bidi_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client._remote_max_streams_bidi, 128)

        # client receives MAX_STREAMS_BIDI raising limit
        client._handle_max_streams_bidi_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_STREAMS_BIDI,
            Buffer(data=encode_uint_var(129)),
        )
        self.assertEqual(client._remote_max_streams_bidi, 129)

        # client receives MAX_STREAMS_BIDI lowering limit
        client._handle_max_streams_bidi_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_STREAMS_BIDI,
            Buffer(data=encode_uint_var(127)),
        )
        self.assertEqual(client._remote_max_streams_bidi, 129)

    def test_handle_max_streams_uni_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)
        self.assertEqual(client._remote_max_streams_uni, 128)

        # client receives MAX_STREAMS_UNI raising limit
        client._handle_max_streams_uni_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_STREAMS_UNI,
            Buffer(data=encode_uint_var(129)),
        )
        self.assertEqual(client._remote_max_streams_uni, 129)

        # client receives MAX_STREAMS_UNI raising limit
        client._handle_max_streams_uni_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.MAX_STREAMS_UNI,
            Buffer(data=encode_uint_var(127)),
        )
        self.assertEqual(client._remote_max_streams_uni, 129)

    def test_handle_new_connection_id_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives NEW_CONNECTION_ID
        client._handle_new_connection_id_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.NEW_CONNECTION_ID,
            Buffer(
                data=binascii.unhexlify(
                    "02117813f3d9e45e0cacbb491b4b66b039f20406f68fede38ec4c31aba8ab1245244e8"
                )
            ),
        )

    def test_handle_new_token_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives NEW_TOKEN
        client._handle_new_token_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.NEW_TOKEN,
            Buffer(data=binascii.unhexlify("080102030405060708")),
        )

    def test_handle_path_challenge_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # server sends PATH_CHALLENGE
        server._send_path_challenge()

    def test_handle_path_response_frame_bad(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # server receives unsollicited PATH_RESPONSE
        with self.assertRaises(QuicConnectionError) as cm:
            server._handle_path_response_frame(
                tls.Epoch.ONE_RTT,
                QuicFrameType.PATH_RESPONSE,
                Buffer(data=b"\x11\x22\x33\x44\x55\x66\x77\x88"),
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.PROTOCOL_VIOLATION)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.PATH_RESPONSE)

    def test_handle_reset_stream_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates bidirectional stream 0
        client.create_stream()

        # client receives RESET_STREAM
        client._handle_reset_stream_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.RESET_STREAM,
            Buffer(data=binascii.unhexlify("001122000001")),
        )

    def test_handle_reset_stream_frame_send_only(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates unidirectional stream 2
        client.create_stream(is_unidirectional=True)

        # client receives RESET_STREAM
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_reset_stream_frame(
                tls.Epoch.ONE_RTT,
                QuicFrameType.RESET_STREAM,
                Buffer(data=binascii.unhexlify("021122000001")),
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.RESET_STREAM)
        self.assertEqual(cm.exception.reason_phrase, "Stream is send-only")

    def test_handle_retire_connection_id_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives RETIRE_CONNECTION_ID
        client._handle_retire_connection_id_frame(
            tls.Epoch.ONE_RTT, QuicFrameType.RETIRE_CONNECTION_ID, Buffer(data=b"\x02")
        )

    def test_handle_stop_sending_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates bidirectional stream 0
        client.create_stream()

        # client receives STOP_SENDING
        client._handle_stop_sending_frame(
            tls.Epoch.ONE_RTT, QuicFrameType.STOP_SENDING, Buffer(data=b"\x00\x11\x22")
        )

    def test_handle_stop_sending_frame_receive_only(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # server creates unidirectional stream 3
        server.create_stream(is_unidirectional=True)

        # client receives STOP_SENDING
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_stop_sending_frame(
                tls.Epoch.ONE_RTT,
                QuicFrameType.STOP_SENDING,
                Buffer(data=b"\x03\x11\x22"),
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.STOP_SENDING)
        self.assertEqual(cm.exception.reason_phrase, "Stream is receive-only")

    def test_handle_stream_frame_over_max_stream_data(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives STREAM frame
        frame_type = QuicFrameType.STREAM_BASE | QuicStreamFlag.OFF
        stream_id = 1
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_stream_frame(
                tls.Epoch.ONE_RTT,
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
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives STREAM frame
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_stream_frame(
                tls.Epoch.ONE_RTT,
                QuicFrameType.STREAM_BASE,
                Buffer(data=encode_uint_var(client._local_max_stream_data_uni * 4 + 3)),
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_LIMIT_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_BASE)
        self.assertEqual(cm.exception.reason_phrase, "Too many streams open")

    def test_handle_stream_frame_send_only(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates unidirectional stream 2
        client.create_stream(is_unidirectional=True)

        # client receives STREAM frame
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_stream_frame(
                tls.Epoch.ONE_RTT, QuicFrameType.STREAM_BASE, Buffer(data=b"\x02")
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_BASE)
        self.assertEqual(cm.exception.reason_phrase, "Stream is send-only")

    def test_handle_stream_frame_wrong_initiator(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives STREAM frame
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_stream_frame(
                tls.Epoch.ONE_RTT, QuicFrameType.STREAM_BASE, Buffer(data=b"\x00")
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_BASE)
        self.assertEqual(cm.exception.reason_phrase, "Wrong stream initiator")

    def test_handle_stream_data_blocked_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates bidirectional stream 0
        client.create_stream()

        # client receives STREAM_DATA_BLOCKED
        client._handle_stream_data_blocked_frame(
            tls.Epoch.ONE_RTT,
            QuicFrameType.STREAM_DATA_BLOCKED,
            Buffer(data=b"\x00\x01"),
        )

    def test_handle_stream_data_blocked_frame_send_only(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client creates unidirectional stream 2
        client.create_stream(is_unidirectional=True)

        # client receives STREAM_DATA_BLOCKED
        with self.assertRaises(QuicConnectionError) as cm:
            client._handle_stream_data_blocked_frame(
                tls.Epoch.ONE_RTT,
                QuicFrameType.STREAM_DATA_BLOCKED,
                Buffer(data=b"\x02\x01"),
            )
        self.assertEqual(cm.exception.error_code, QuicErrorCode.STREAM_STATE_ERROR)
        self.assertEqual(cm.exception.frame_type, QuicFrameType.STREAM_DATA_BLOCKED)
        self.assertEqual(cm.exception.reason_phrase, "Stream is send-only")

    def test_handle_streams_blocked_uni_frame(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives STREAMS_BLOCKED_UNI: 0
        client._handle_streams_blocked_frame(
            tls.Epoch.ONE_RTT, QuicFrameType.STREAMS_BLOCKED_UNI, Buffer(data=b"\x00")
        )

    def test_handle_unknown_frame(self):
        client = QuicConnection(is_client=True)

        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

        # perform handshake
        client_transport, server_transport = create_transport(client, server)

        # client receives unknown frame
        with self.assertRaises(QuicConnectionError) as cm:
            client._payload_received(tls.Epoch.ONE_RTT, b"\x1e")
        self.assertEqual(cm.exception.error_code, QuicErrorCode.PROTOCOL_VIOLATION)
        self.assertEqual(cm.exception.frame_type, 0x1E)
        self.assertEqual(cm.exception.reason_phrase, "Unexpected frame type")

    def test_stream_direction(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY,
        )

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
        client = QuicConnection(is_client=True)
        client.supported_versions = [QuicProtocolVersion.DRAFT_19]

        client_transport = FakeTransport()
        client.connection_made(client_transport)
        self.assertEqual(client_transport.sent, 1)

        # no common version, no retry
        client.datagram_received(load("version_negotiation.bin"), None)
        self.assertEqual(client_transport.sent, 1)

    def test_version_negotiation_ok(self):
        client = QuicConnection(is_client=True)

        client_transport = FakeTransport()
        client.connection_made(client_transport)
        self.assertEqual(client_transport.sent, 1)

        # found a common version, retry
        client.datagram_received(load("version_negotiation.bin"), None)
        self.assertEqual(client_transport.sent, 2)
