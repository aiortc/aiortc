import binascii
from unittest import TestCase

from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, RequestReceived, ResponseReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived

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


class FakeQuicConnection:
    def __init__(self, configuration):
        self.configuration = configuration
        self.stream_queue = []
        self._next_stream_bidi = 0 if configuration.is_client else 1
        self._next_stream_uni = 2 if configuration.is_client else 3

    def get_next_available_stream_id(self, is_unidirectional=False):
        if is_unidirectional:
            stream_id = self._next_stream_uni
            self._next_stream_uni += 4
        else:
            stream_id = self._next_stream_bidi
            self._next_stream_bidi += 4
        return stream_id

    def send_stream_data(self, stream_id, data, end_stream=False):
        # chop up data into individual bytes
        for c in data:
            self.stream_queue.append(
                StreamDataReceived(
                    data=bytes([c]), end_stream=False, stream_id=stream_id
                )
            )
        if end_stream:
            self.stream_queue.append(
                StreamDataReceived(data=b"", end_stream=end_stream, stream_id=stream_id)
            )


class H3ConnectionTest(TestCase):
    def _make_request(self, h3_client, h3_server):
        quic_client = h3_client._quic
        quic_server = h3_server._quic

        # send request
        stream_id = quic_client.get_next_available_stream_id()
        h3_client.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", b"localhost"),
                (b":path", b"/"),
                (b"x-foo", b"client"),
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
                (b"x-foo", b"client"),
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
                (b"x-foo", b"server"),
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
            [
                (b":status", b"200"),
                (b"content-type", b"text/html; charset=utf-8"),
                (b"x-foo", b"server"),
            ],
        )
        self.assertEqual(events[0].stream_id, stream_id)
        self.assertEqual(events[0].stream_ended, False)

        self.assertTrue(isinstance(events[1], DataReceived))
        self.assertEqual(events[1].data, b"<html><body>hello</body></html>")
        self.assertEqual(events[1].stream_id, stream_id)
        self.assertEqual(events[1].stream_ended, True)

    def test_connect(self):
        with client_and_server(
            client_options={"alpn_protocols": ["h3-22"]},
            server_options={"alpn_protocols": ["h3-22"]},
        ) as (quic_client, quic_server):
            h3_client = H3Connection(quic_client)
            h3_server = H3Connection(quic_server)

            # make first request
            self._make_request(h3_client, h3_server)

            # make second request
            self._make_request(h3_client, h3_server)

            # make third request -> dynamic table
            self._make_request(h3_client, h3_server)

    def test_fragmented_frame(self):
        quic_client = FakeQuicConnection(
            configuration=QuicConfiguration(is_client=True)
        )
        quic_server = FakeQuicConnection(
            configuration=QuicConfiguration(is_client=False)
        )

        h3_client = H3Connection(quic_client)
        h3_server = H3Connection(quic_server)

        # send headers
        stream_id = quic_client.get_next_available_stream_id()
        h3_client.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", b"localhost"),
                (b":path", b"/"),
                (b"x-foo", b"client"),
            ],
        )
        http_events = []
        for event in quic_client.stream_queue:
            http_events.extend(h3_server.handle_event(event))
        quic_client.stream_queue.clear()
        self.assertEqual(
            http_events,
            [
                RequestReceived(
                    headers=[
                        (b":method", b"GET"),
                        (b":scheme", b"https"),
                        (b":authority", b"localhost"),
                        (b":path", b"/"),
                        (b"x-foo", b"client"),
                    ],
                    stream_id=0,
                    stream_ended=False,
                )
            ],
        )

        # send body
        h3_client.send_data(stream_id=stream_id, data=b"hello", end_stream=True)
        http_events = []
        for event in quic_client.stream_queue:
            http_events.extend(h3_server.handle_event(event))
        quic_client.stream_queue.clear()
        self.assertEqual(
            http_events,
            [
                DataReceived(data=b"h", stream_id=0, stream_ended=False),
                DataReceived(data=b"e", stream_id=0, stream_ended=False),
                DataReceived(data=b"l", stream_id=0, stream_ended=False),
                DataReceived(data=b"l", stream_id=0, stream_ended=False),
                DataReceived(data=b"o", stream_id=0, stream_ended=False),
                DataReceived(data=b"", stream_id=0, stream_ended=True),
            ],
        )

    def test_blocked_stream(self):
        quic_client = FakeQuicConnection(
            configuration=QuicConfiguration(is_client=True)
        )
        h3_client = H3Connection(quic_client)

        h3_client.handle_event(
            StreamDataReceived(
                stream_id=3,
                data=binascii.unhexlify(
                    "0004170150000680020000074064091040bcc0000000faceb00c"
                ),
                end_stream=False,
            )
        )
        h3_client.handle_event(
            StreamDataReceived(stream_id=7, data=b"\x02", end_stream=False)
        )
        h3_client.handle_event(
            StreamDataReceived(stream_id=11, data=b"\x03", end_stream=False)
        )
        h3_client.handle_event(
            StreamDataReceived(
                stream_id=0, data=binascii.unhexlify("01040280d910"), end_stream=False
            )
        )
        h3_client.handle_event(
            StreamDataReceived(
                stream_id=0,
                data=binascii.unhexlify(
                    "00408d796f752072656163686564206d766673742e6e65742c20726561636820"
                    "746865202f6563686f20656e64706f696e7420666f7220616e206563686f2072"
                    "6573706f6e7365207175657279202f3c6e756d6265723e20656e64706f696e74"
                    "7320666f722061207661726961626c652073697a6520726573706f6e73652077"
                    "6974682072616e646f6d206279746573"
                ),
                end_stream=True,
            )
        )
        self.assertEqual(
            h3_client.handle_event(
                StreamDataReceived(
                    stream_id=7,
                    data=binascii.unhexlify(
                        "3fe101c696d07abe941094cb6d0a08017d403971966e32ca98b46f"
                    ),
                    end_stream=False,
                )
            ),
            [
                ResponseReceived(
                    headers=[
                        (b":status", b"200"),
                        (b"date", b"Mon, 22 Jul 2019 06:33:33 GMT"),
                    ],
                    stream_id=0,
                    stream_ended=False,
                ),
                DataReceived(
                    data=(
                        b"you reached mvfst.net, reach the /echo endpoint for an "
                        b"echo response query /<number> endpoints for a variable "
                        b"size response with random bytes"
                    ),
                    stream_id=0,
                    stream_ended=True,
                ),
            ],
        )

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
            self.assertEqual(list(h3_server._stream.keys()), [2])
            self.assertEqual(h3_server._stream[2].buffer, b"")
            self.assertEqual(h3_server._stream[2].stream_type, 9)

            # unknown stream type 64, one byte at a time
            stream_id = quic_client.get_next_available_stream_id(is_unidirectional=True)
            self.assertEqual(stream_id, 6)

            quic_client.send_stream_data(stream_id, b"\x40")
            self.assertEqual(h3_transfer(quic_client, h3_server), [])
            self.assertEqual(list(h3_server._stream.keys()), [2, 6])
            self.assertEqual(h3_server._stream[2].buffer, b"")
            self.assertEqual(h3_server._stream[2].stream_type, 9)
            self.assertEqual(h3_server._stream[6].buffer, b"\x40")
            self.assertEqual(h3_server._stream[6].stream_type, None)

            quic_client.send_stream_data(stream_id, b"\x40")
            self.assertEqual(h3_transfer(quic_client, h3_server), [])
            self.assertEqual(list(h3_server._stream.keys()), [2, 6])
            self.assertEqual(h3_server._stream[2].buffer, b"")
            self.assertEqual(h3_server._stream[2].stream_type, 9)
            self.assertEqual(h3_server._stream[6].buffer, b"")
            self.assertEqual(h3_server._stream[6].stream_type, 64)
