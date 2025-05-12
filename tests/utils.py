import asyncio
import contextlib
import functools
import logging
import os
import sys
import unittest
from collections.abc import AsyncGenerator, Callable, Coroutine
from typing import Optional, TypeVar, cast

if sys.version_info >= (3, 10):
    from typing import ParamSpec
else:
    from typing_extensions import ParamSpec

from aiortc.rtcdtlstransport import RTCCertificate, RTCDtlsTransport
from aiortc.rtcicetransport import RTCIceTransport

P = ParamSpec("P")
T = TypeVar("T")


def lf2crlf(x: str) -> str:
    return x.replace("\n", "\r\n")


class ClosedDtlsTransport:
    state = "closed"


class DummyConnection:
    def __init__(
        self,
        rx_queue: asyncio.Queue[bytes],
        tx_queue: asyncio.Queue[bytes],
    ) -> None:
        self.closed = False
        self.loss_cursor = 0
        self.loss_pattern: Optional[list[bool]] = None
        self.rx_queue = rx_queue
        self.tx_queue = tx_queue

    async def close(self) -> None:
        if not self.closed:
            await self.rx_queue.put(None)
            self.closed = True

    async def recv(self) -> bytes:
        if self.closed:
            raise ConnectionError

        data = await self.rx_queue.get()
        if data is None:
            raise ConnectionError
        return data

    async def send(self, data: bytes) -> None:
        if self.closed:
            raise ConnectionError

        if self.loss_pattern is not None:
            lost = self.loss_pattern[self.loss_cursor]
            self.loss_cursor = (self.loss_cursor + 1) % len(self.loss_pattern)
            if lost:
                return

        await self.tx_queue.put(data)


class DummyIceTransport:
    def __init__(self, connection: DummyConnection, role: str) -> None:
        self._connection = connection
        self.role = role

    async def stop(self) -> None:
        await self._connection.close()

    async def _recv(self) -> bytes:
        return await self._connection.recv()

    async def _send(self, data: bytes) -> None:
        await self._connection.send(data)


class TestCase(unittest.TestCase):
    def ensureIsInstance(self, obj: object, cls: type[T]) -> T:
        self.assertIsInstance(obj, cls)
        return cast(T, obj)


def asynctest(
    coro: Callable[P, Coroutine[None, None, None]],
) -> Callable[P, None]:
    @functools.wraps(coro)
    def wrap(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(coro(*args, **kwargs))

    return wrap


def dummy_connection_pair() -> tuple[DummyConnection, DummyConnection]:
    queue_a: asyncio.Queue[bytes] = asyncio.Queue()
    queue_b: asyncio.Queue[bytes] = asyncio.Queue()
    return (
        DummyConnection(rx_queue=queue_a, tx_queue=queue_b),
        DummyConnection(rx_queue=queue_b, tx_queue=queue_a),
    )


def dummy_ice_transport_pair() -> tuple[RTCIceTransport, RTCIceTransport]:
    connection_a, connection_b = dummy_connection_pair()
    return (
        cast(RTCIceTransport, DummyIceTransport(connection_a, "controlling")),
        cast(RTCIceTransport, DummyIceTransport(connection_b, "controlled")),
    )


@contextlib.asynccontextmanager
async def dummy_dtls_transport_pair() -> AsyncGenerator[
    tuple[RTCDtlsTransport, RTCDtlsTransport], None
]:
    ice_a, ice_b = dummy_ice_transport_pair()
    dtls_a = RTCDtlsTransport(ice_a, [RTCCertificate.generateCertificate()])
    dtls_b = RTCDtlsTransport(ice_b, [RTCCertificate.generateCertificate()])
    await asyncio.gather(
        dtls_b.start(dtls_a.getLocalParameters()),
        dtls_a.start(dtls_b.getLocalParameters()),
    )

    try:
        yield (dtls_a, dtls_b)
    finally:
        await dtls_a.stop()
        await dtls_b.stop()


def load(name: str) -> bytes:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "rb") as fp:
        return fp.read()


def set_loss_pattern(transport: RTCIceTransport, loss_pattern: list[bool]) -> None:
    connection = cast(DummyConnection, transport._connection)
    connection.loss_pattern = loss_pattern


if os.environ.get("AIORTC_DEBUG"):
    logging.basicConfig(level=logging.DEBUG)
