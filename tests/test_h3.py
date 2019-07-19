from unittest import TestCase

from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, RequestReceived, ResponseReceived

from .test_connection import client_and_server, transfer


def h3_transfer(quic_sender, h3_receiver):
    quic_receiver = h3_receiver._quic
    transfer(quic_sender, quic_receiver)

    # process QUIC events
    http_events = []
    event = quic_receiver.next_event()
    while event is not None:
        http_events.extend(h3_receiver.handle_event(event))
        event = quic_receiver.next_event()
    return http_events


class H3ConnectionTest(TestCase):
    def test_connect(self):
        with client_and_server(
            client_options={"alpn_protocols": ["h3-22"]},
            server_options={"alpn_protocols": ["h3-22"]},
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
                    (b":authority", b"localhost"),
                    (b":path", b"/"),
                ],
            )
            h3_client.send_data(stream_id=stream_id, data=b"", end_stream=True)

            # receive request
            events = h3_transfer(quic_client, h3_server)
            self.assertEqual(len(events), 2)

            self.assertTrue(isinstance(events[0], RequestReceived))
            self.assertEqual(
                events[0].headers,
                [
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":authority", b"localhost"),
                    (b":path", b"/"),
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

            # receive response
            events = h3_transfer(quic_server, h3_client)
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
            client_options={"alpn_protocols": ["h3-22"]},
            server_options={"alpn_protocols": ["h3-22"]},
        ) as (quic_client, quic_server):
            h3_server = H3Connection(quic_server)

            # unknown stream type 9
            stream_id = quic_client.get_next_available_stream_id(is_unidirectional=True)
            self.assertEqual(stream_id, 2)
            quic_client.send_stream_data(stream_id, b"\x09")
            self.assertEqual(h3_transfer(quic_client, h3_server), [])
            self.assertEqual(h3_server._stream_buffers, {2: b""})
            self.assertEqual(h3_server._stream_types, {2: 9})

            # unknown stream type 64, one byte at a time
            stream_id = quic_client.get_next_available_stream_id(is_unidirectional=True)
            self.assertEqual(stream_id, 6)

            quic_client.send_stream_data(stream_id, b"\x40")
            self.assertEqual(h3_transfer(quic_client, h3_server), [])
            self.assertEqual(h3_server._stream_buffers, {2: b"", 6: b"\x40"})
            self.assertEqual(h3_server._stream_types, {2: 9})

            quic_client.send_stream_data(stream_id, b"\x40")
            self.assertEqual(h3_transfer(quic_client, h3_server), [])
            self.assertEqual(h3_server._stream_buffers, {2: b"", 6: b""})
            self.assertEqual(h3_server._stream_types, {2: 9, 6: 64})
