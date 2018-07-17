import asyncio
import json

from aiortc import RTCSessionDescription


def description_from_string(descr_str):
    descr_dict = json.loads(descr_str)
    return RTCSessionDescription(
        sdp=descr_dict['sdp'],
        type=descr_dict['type'])


def description_to_string(descr):
    return json.dumps({
        'sdp': descr.sdp,
        'type': descr.type
    })


class CopyAndPasteSignaling:
    async def close(self):
        pass

    async def receive(self):
        print('-- Please enter remote description --')
        descr_str = input()
        print()
        return description_from_string(descr_str)

    async def send(self, descr):
        print('-- Your description --')
        print(description_to_string(descr))
        print()


class UnixSocketSignaling:
    def __init__(self):
        self._path = '/tmp/aiortc.sock'
        self._reader = None
        self._writer = None

    async def _connect(self, server):
        if self._writer is not None:
            return

        if server:
            connected = asyncio.Event()

            def client_connected(reader, writer):
                self._reader = reader
                self._writer = writer
                connected.set()

            await asyncio.start_unix_server(client_connected, path=self._path)
            await connected.wait()
        else:
            self._reader, self._writer = await asyncio.open_unix_connection(self._path)

    async def close(self):
        if self._writer is not None:
            self._writer.close()
            self._reader = None
            self._writer = None

    async def receive(self):
        await self._connect(False)
        data = await self._reader.readuntil()
        return description_from_string(data.decode('utf8'))

    async def send(self, descr):
        await self._connect(True)
        data = description_to_string(descr).encode('utf8')
        self._writer.write(data + b'\n')


def add_signaling_arguments(parser):
    parser.add_argument('--signaling', '-s', choices=['copy-and-paste', 'unix-socket'])


def create_signaling(args):
    if args.signaling == 'unix-socket':
        return UnixSocketSignaling()
    else:
        return CopyAndPasteSignaling()
