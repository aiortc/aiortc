import argparse
import asyncio
import io
import os
from collections.abc import Callable, Coroutine
from typing import TypeVar

from aiortc import RTCIceCandidate, RTCSessionDescription
from aiortc.contrib.signaling import (
    BYE,
    CopyAndPasteSignaling,
    TcpSocketSignaling,
    UnixSocketSignaling,
    add_signaling_arguments,
    create_signaling,
    object_from_string,
    object_to_string,
)

from .utils import TestCase, asynctest

T = TypeVar("T")


async def delay(coro: Callable[[], Coroutine[None, None, T]]) -> T:
    await asyncio.sleep(0.1)
    return await coro()


offer = RTCSessionDescription(sdp="some-offer", type="offer")
answer = RTCSessionDescription(sdp="some-answer", type="answer")


class SignalingTest(TestCase):
    def setUp(self) -> None:
        def mock_print(*args: object) -> None:
            pass

        # hijack print()
        self.original_print = __builtins__["print"]  # type: ignore
        __builtins__["print"] = mock_print

    def tearDown(self) -> None:
        # restore print()
        __builtins__["print"] = self.original_print

    @asynctest
    async def test_copy_and_paste(self) -> None:
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(["-s", "copy-and-paste"])

        sig_server = self.ensureIsInstance(
            create_signaling(args), CopyAndPasteSignaling
        )
        sig_client = self.ensureIsInstance(
            create_signaling(args), CopyAndPasteSignaling
        )

        def make_pipes() -> tuple[io.TextIOBase, io.TextIOBase]:
            r, w = os.pipe()
            return os.fdopen(r, "r"), os.fdopen(w, "w")

        # mock out read / write pipes
        sig_server._read_pipe, sig_client._write_pipe = make_pipes()
        sig_client._read_pipe, sig_server._write_pipe = make_pipes()

        # connect
        await sig_server.connect()
        await sig_client.connect()

        res = await asyncio.gather(sig_server.send(offer), delay(sig_client.receive))
        self.assertEqual(res[1], offer)

        res = await asyncio.gather(sig_client.send(answer), delay(sig_server.receive))
        self.assertEqual(res[1], answer)

        await asyncio.gather(sig_server.close(), sig_client.close())

        # cleanup mocks
        sig_client._write_pipe.close()
        sig_server._write_pipe.close()

    @asynctest
    async def test_tcp_socket(self) -> None:
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(["-s", "tcp-socket"])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        # connect
        await sig_server.connect()
        await sig_client.connect()

        res = await asyncio.gather(sig_server.send(offer), delay(sig_client.receive))
        self.assertEqual(res[1], offer)

        res = await asyncio.gather(sig_client.send(answer), delay(sig_server.receive))
        self.assertEqual(res[1], answer)

        await asyncio.gather(sig_server.close(), sig_client.close())

    @asynctest
    async def test_tcp_socket_abrupt_disconnect(self) -> None:
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(["-s", "tcp-socket"])

        sig_server = self.ensureIsInstance(create_signaling(args), TcpSocketSignaling)
        sig_client = self.ensureIsInstance(create_signaling(args), TcpSocketSignaling)

        # connect
        await sig_server.connect()
        await sig_client.connect()

        res = await asyncio.gather(sig_server.send(offer), delay(sig_client.receive))
        self.assertEqual(res[1], offer)

        # break connection
        sig_client._writer.close()
        sig_server._writer.close()

        obj = await sig_server.receive()
        self.assertIsNone(obj)

        obj = await sig_client.receive()
        self.assertIsNone(obj)

        await asyncio.gather(sig_server.close(), sig_client.close())

    @asynctest
    async def test_unix_socket(self) -> None:
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(["-s", "unix-socket"])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        # connect
        await sig_server.connect()
        await sig_client.connect()

        res = await asyncio.gather(sig_server.send(offer), delay(sig_client.receive))
        self.assertEqual(res[1], offer)

        res = await asyncio.gather(sig_client.send(answer), delay(sig_server.receive))
        self.assertEqual(res[1], answer)

        await asyncio.gather(sig_server.close(), sig_client.close())

    @asynctest
    async def test_unix_socket_abrupt_disconnect(self) -> None:
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(["-s", "unix-socket"])

        sig_server = self.ensureIsInstance(create_signaling(args), UnixSocketSignaling)
        sig_client = self.ensureIsInstance(create_signaling(args), UnixSocketSignaling)

        # connect
        await sig_server.connect()
        await sig_client.connect()

        res = await asyncio.gather(sig_server.send(offer), delay(sig_client.receive))
        self.assertEqual(res[1], offer)

        # break connection
        sig_client._writer.close()
        sig_server._writer.close()

        obj = await sig_server.receive()
        self.assertIsNone(obj)

        obj = await sig_client.receive()
        self.assertIsNone(obj)

        await asyncio.gather(sig_server.close(), sig_client.close())


class SignalingUtilsTest(TestCase):
    def test_bye_from_string(self) -> None:
        self.assertEqual(object_from_string('{"type": "bye"}'), BYE)

    def test_bye_to_string(self) -> None:
        self.assertEqual(object_to_string(BYE), '{"type": "bye"}')

    def test_candidate_from_string(self) -> None:
        candidate = self.ensureIsInstance(
            object_from_string(
                '{"candidate": "candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ '
                'host", "id": "audio", "label": 0, "type": "candidate"}'
            ),
            RTCIceCandidate,
        )
        self.assertEqual(candidate.component, 1)
        self.assertEqual(candidate.foundation, "0")
        self.assertEqual(candidate.ip, "192.168.99.7")
        self.assertEqual(candidate.port, 33543)
        self.assertEqual(candidate.priority, 2122252543)
        self.assertEqual(candidate.protocol, "UDP")
        self.assertEqual(candidate.sdpMid, "audio")
        self.assertEqual(candidate.sdpMLineIndex, 0)
        self.assertEqual(candidate.type, "host")

    def test_candidate_to_string(self) -> None:
        candidate = RTCIceCandidate(
            component=1,
            foundation="0",
            ip="192.168.99.7",
            port=33543,
            priority=2122252543,
            protocol="UDP",
            type="host",
        )
        candidate.sdpMid = "audio"
        candidate.sdpMLineIndex = 0
        self.assertEqual(
            object_to_string(candidate),
            '{"candidate": "candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ host", '
            '"id": "audio", "label": 0, "type": "candidate"}',
        )
