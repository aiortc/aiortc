import asyncio
import logging
import os
import sys

from cryptography.hazmat.backends.openssl.backend import backend

from aiortc.rtcdtlstransport import RTCCertificate, RTCDtlsTransport


def lf2crlf(x):
    return x.replace('\n', '\r\n')


class DummyConnection:
    def __init__(self, rx_queue, tx_queue):
        self.closed = False
        self.loss_cursor = 0
        self.loss_pattern = None
        self.rx_queue = rx_queue
        self.tx_queue = tx_queue

    async def close(self):
        if not self.closed:
            await self.rx_queue.put(None)
            self.closed = True

    async def recv(self):
        if self.closed:
            raise ConnectionError

        data = await self.rx_queue.get()
        if data is None:
            raise ConnectionError
        return data

    async def send(self, data):
        if self.closed:
            raise ConnectionError

        if self.loss_pattern is not None:
            lost = self.loss_pattern[self.loss_cursor]
            self.loss_cursor = (self.loss_cursor + 1) % len(self.loss_pattern)
            if lost:
                return

        await self.tx_queue.put(data)


class DummyIceTransport:
    def __init__(self, connection, role):
        self._connection = connection
        self.role = role

    async def stop(self):
        await self._connection.close()

    async def _recv(self):
        return await self._connection.recv()

    async def _send(self, data):
        await self._connection.send(data)


def dummy_connection_pair():
    queue_a = asyncio.Queue()
    queue_b = asyncio.Queue()
    return (
        DummyConnection(rx_queue=queue_a, tx_queue=queue_b),
        DummyConnection(rx_queue=queue_b, tx_queue=queue_a),
    )


def dummy_ice_transport_pair():
    connection_a, connection_b = dummy_connection_pair()
    return (
        DummyIceTransport(connection_a, 'controlling'),
        DummyIceTransport(connection_b, 'controlled')
    )


def dummy_dtls_transport_pair():
    ice_a, ice_b = dummy_ice_transport_pair()
    dtls_a = RTCDtlsTransport(ice_a, [RTCCertificate.generateCertificate()])
    dtls_b = RTCDtlsTransport(ice_b, [RTCCertificate.generateCertificate()])
    run(asyncio.gather(
        dtls_b.start(dtls_a.getLocalParameters()),
        dtls_a.start(dtls_b.getLocalParameters())
    ))
    return (dtls_a, dtls_b)


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


if os.environ.get('AIORTC_DEBUG'):
    logging.basicConfig(level=logging.DEBUG)

if os.environ.get('TRAVIS'):
    sys.stderr.write(backend.openssl_version_text() + '\n')
