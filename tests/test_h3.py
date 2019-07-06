from unittest import TestCase

from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, RequestReceived, ResponseReceived

from .test_connection import client_and_server, transfer


class H3ConnectionTest(TestCase):
    def test_connect(self):
        with client_and_server(
            client_options={"alpn_protocols": ["h3-20"]},
            server_options={"alpn_protocols": ["h3-20"]},
        ) as (quic_client, quic_server):
            h3_client = H3Connection(quic_client)
            h3_server = H3Connection(quic_server)

            # send request
            stream_id = quic_client.get_next_available_stream_id()
            h3_client.send_headers(
                stream_id=stream_id,
                headers=[
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":path", b"/"),
                    (b"host", b"localhost"),
                ],
            )
            h3_client.send_data(stream_id=stream_id, data=b"", end_stream=True)
            self.assertEqual(h3_client._update(), [])

            # receive request
            transfer(quic_client, quic_server)
            events = h3_server._update()
            self.assertEqual(len(events), 2)

            self.assertTrue(isinstance(events[0], RequestReceived))
            self.assertEqual(
                events[0].headers,
                [
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":path", b"/"),
                    (b"host", b"localhost"),
                ],
            )
            self.assertEqual(events[0].stream_id, stream_id)
            self.assertEqual(events[0].stream_ended, False)

            self.assertTrue(isinstance(events[1], DataReceived))
            self.assertEqual(events[1].data, b"")
            self.assertEqual(events[1].stream_id, stream_id)
            self.assertEqual(events[1].stream_ended, True)

            # send response
            h3_server.send_headers(
                stream_id=stream_id,
                headers=[
                    (b":status", b"200"),
                    (b"content-type", b"text/html; charset=utf-8"),
                ],
            )
            h3_server.send_data(
                stream_id=stream_id,
                data=b"<html><body>hello</body></html>",
                end_stream=True,
            )
            self.assertEqual(h3_server._update(), [])

            # receive response
            transfer(quic_server, quic_client)
            events = h3_client._update()
            self.assertEqual(len(events), 2)

            self.assertTrue(isinstance(events[0], ResponseReceived))
            self.assertEqual(
                events[0].headers,
                [(b":status", b"200"), (b"content-type", b"text/html; charset=utf-8")],
            )
            self.assertEqual(events[0].stream_id, stream_id)
            self.assertEqual(events[0].stream_ended, False)

            self.assertTrue(isinstance(events[1], DataReceived))
            self.assertEqual(events[1].data, b"<html><body>hello</body></html>")
            self.assertEqual(events[1].stream_id, stream_id)
            self.assertEqual(events[1].stream_ended, True)

    def test_uni_stream_type(self):
        with client_and_server(
            client_options={"alpn_protocols": ["h3-20"]},
            server_options={"alpn_protocols": ["h3-20"]},
        ) as (quic_client, quic_server):
            h3_server = H3Connection(quic_server)

            # unknown stream type 9
            stream_id = quic_client.get_next_available_stream_id(is_unidirectional=True)
            self.assertEqual(stream_id, 2)
            quic_client.send_stream_data(stream_id, b"\x09")
            transfer(quic_client, quic_server)
            self.assertEqual(h3_server._update(), [])
            self.assertEqual(h3_server._stream_buffers, {2: b""})
            self.assertEqual(h3_server._stream_types, {2: 9})

            # unknown stream type 64, one byte at a time
            stream_id = quic_client.get_next_available_stream_id(is_unidirectional=True)
            self.assertEqual(stream_id, 6)

            quic_client.send_stream_data(stream_id, b"\x40")
            transfer(quic_client, quic_server)
            self.assertEqual(h3_server._update(), [])
            self.assertEqual(h3_server._stream_buffers, {2: b"", 6: b"\x40"})
            self.assertEqual(h3_server._stream_types, {2: 9})

            quic_client.send_stream_data(stream_id, b"\x40")
            transfer(quic_client, quic_server)
            self.assertEqual(h3_server._update(), [])
            self.assertEqual(h3_server._stream_buffers, {2: b"", 6: b""})
            self.assertEqual(h3_server._stream_types, {2: 9, 6: 64})
