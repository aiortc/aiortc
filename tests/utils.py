import asyncio
import os


def dummy_transport_pair():
    queue_a = asyncio.Queue()
    queue_b = asyncio.Queue()
    return (
        DummyTransport(rx_queue=queue_a, tx_queue=queue_b),
        DummyTransport(rx_queue=queue_b, tx_queue=queue_a),
    )


class DummyTransport:
    def __init__(self, rx_queue, tx_queue):
        self.rx_queue = rx_queue
        self.tx_queue = tx_queue

    async def recv(self):
        return await self.rx_queue.get()

    async def send(self, data):
        await self.tx_queue.put(data)


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)
