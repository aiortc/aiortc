import asyncio
import logging
import os

from aiortc import clock
from aiortc.stats import RTCStatsReport, RTCTransportStats
from aiortc.utils import first_completed


class DummyConnection:
    def __init__(self, rx_queue, tx_queue, loss):
        self.closed = asyncio.Event()
        self.loss_cursor = 0
        self.loss_pattern = loss
        self.rx_queue = rx_queue
        self.tx_queue = tx_queue

    async def close(self):
        self.closed.set()

    async def recv(self):
        data = await first_completed(self.rx_queue.get(), self.closed.wait())
        if data is True:
            raise ConnectionError
        return data

    async def send(self, data):
        if self.closed.is_set():
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


class DummyDtlsTransport:
    def __init__(self, transport, state='connected'):
        self.data = transport._connection
        self.state = state
        self.transport = transport
        self._data_handle = None
        self._data_receiver = None
        self._stats_id = 'transport_' + str(id(self))

    async def stop(self):
        await self.transport.stop()
        self.state = 'closed'

    def _get_stats(self):
        report = RTCStatsReport()
        report.add(RTCTransportStats(
            # RTCStats
            timestamp=clock.current_datetime(),
            type='transport',
            id=self._stats_id,
            # RTCTransportStats,
            packetsSent=0,
            packetsReceived=0,
            bytesSent=0,
            bytesReceived=0,
            iceRole=self.transport.role,
            dtlsState=self.state,
        ))
        return report

    def _register_data_receiver(self, receiver):
        assert self._data_receiver is None
        self._data_receiver = receiver
        self._data_handle = asyncio.ensure_future(self.__run())

    def _register_rtp_receiver(self, receiver, parameters):
        pass

    def _register_rtp_sender(self, sender, parameters):
        pass

    async def _send_data(self, data):
        await self.transport._connection.send(data)

    async def _send_rtp(self, data):
        await self.transport._connection.send(data)

    def _unregister_data_receiver(self, receiver):
        if self._data_receiver == receiver:
            self._data_receiver = None
            self._data_handle.cancel()
            self._data_handle = None

    def _unregister_rtp_receiver(self, receiver):
        pass

    def _unregister_rtp_sender(self, sender):
        pass

    async def __run(self):
        while True:
            try:
                data = await self.transport._connection.recv()
            except ConnectionError:
                break
            await self._data_receiver._handle_data(data)


def dummy_connection_pair(loss=None):
    queue_a = asyncio.Queue()
    queue_b = asyncio.Queue()
    return (
        DummyConnection(rx_queue=queue_a, tx_queue=queue_b, loss=loss),
        DummyConnection(rx_queue=queue_b, tx_queue=queue_a, loss=loss),
    )


def dummy_ice_transport_pair(loss=None):
    connection_a, connection_b = dummy_connection_pair(loss=loss)
    return (
        DummyIceTransport(connection_a, 'controlling'),
        DummyIceTransport(connection_b, 'controlled')
    )


def dummy_dtls_transport_pair(loss=None):
    ice_a, ice_b = dummy_ice_transport_pair(loss=loss)
    return (
        DummyDtlsTransport(ice_a),
        DummyDtlsTransport(ice_b)
    )


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


if os.environ.get('AIORTC_DEBUG'):
    logging.basicConfig(level=logging.DEBUG)
