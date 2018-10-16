import asyncio
import json
import os
import sys

from aiortc import RTCIceCandidate, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp


def object_from_string(message_str):
    message = json.loads(message_str)
    if message['type'] in ['answer', 'offer']:
        return RTCSessionDescription(**message)
    elif message['type'] == 'candidate':
        candidate = candidate_from_sdp(message['candidate'].split(':', 1)[1])
        candidate.sdpMid = message['id']
        candidate.sdpMLineIndex = message['label']
        return candidate


def object_to_string(obj):
    if isinstance(obj, RTCSessionDescription):
        message = {
            'sdp': obj.sdp,
            'type': obj.type
        }
    elif isinstance(obj, RTCIceCandidate):
        message = {
            'candidate': 'candidate:' + candidate_to_sdp(obj),
            'id': obj.sdpMid,
            'label': obj.sdpMLineIndex,
            'type': 'candidate',
        }
    else:
        message = {'type': 'bye'}
    return json.dumps(message, sort_keys=True)


class CopyAndPasteSignaling:
    def __init__(self):
        self._read_pipe = sys.stdin
        self._read_transport = None
        self._reader = None
        self._write_pipe = sys.stdout

    async def _connect(self):
        if self._reader is not None:
            return

        loop = asyncio.get_event_loop()
        self._reader = asyncio.StreamReader(loop=loop)
        self._read_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(self._reader),
            self._read_pipe)

    async def close(self):
        if self._reader is not None:
            self._read_transport.close()
            self._reader = None

    async def receive(self):
        await self._connect()
        print('-- Please enter a message from remote party --')
        descr_str = await self._reader.readline()
        print()
        return object_from_string(descr_str)

    async def send(self, descr):
        print('-- Please send this message to the remote party --')
        self._write_pipe.write(object_to_string(descr) + '\n')
        print()


class TcpSocketSignaling:
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._server = None
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

            self._server = await asyncio.start_server(
                client_connected,
                host=self._host,
                port=self._port)
            await connected.wait()
        else:
            self._reader, self._writer = await asyncio.open_connection(
                host=self._host,
                port=self._port)

    async def close(self):
        if self._writer is not None:
            await self.send(None)
            self._writer.close()
            self._reader = None
            self._writer = None
        if self._server is not None:
            self._server.close()
            self._server = None

    async def receive(self):
        await self._connect(False)
        try:
            data = await self._reader.readuntil()
        except asyncio.IncompleteReadError:
            return
        return object_from_string(data.decode('utf8'))

    async def send(self, descr):
        await self._connect(True)
        data = object_to_string(descr).encode('utf8')
        self._writer.write(data + b'\n')


class UnixSocketSignaling:
    def __init__(self, path):
        self._path = path
        self._server = None
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

            self._server = await asyncio.start_unix_server(client_connected, path=self._path)
            await connected.wait()
        else:
            self._reader, self._writer = await asyncio.open_unix_connection(self._path)

    async def close(self):
        if self._writer is not None:
            await self.send(None)
            self._writer.close()
            self._reader = None
            self._writer = None
        if self._server is not None:
            self._server.close()
            self._server = None
            os.unlink(self._path)

    async def receive(self):
        await self._connect(False)
        try:
            data = await self._reader.readuntil()
        except asyncio.IncompleteReadError:
            return
        return object_from_string(data.decode('utf8'))

    async def send(self, descr):
        await self._connect(True)
        data = object_to_string(descr).encode('utf8')
        self._writer.write(data + b'\n')


def add_signaling_arguments(parser):
    """
    Add signaling method arguments to an argparse.ArgumentParser.
    """
    parser.add_argument('--signaling', '-s', choices=[
        'copy-and-paste', 'tcp-socket', 'unix-socket'])
    parser.add_argument('--signaling-host', default='127.0.0.1',
                        help='Signaling host (tcp-socket only)')
    parser.add_argument('--signaling-port', default=1234,
                        help='Signaling port (tcp-socket only)')
    parser.add_argument('--signaling-path', default='aiortc.socket',
                        help='Signaling socket path (unix-socket only)')


def create_signaling(args):
    """
    Create a signaling method based on command-line arguments.
    """
    if args.signaling == 'tcp-socket':
        return TcpSocketSignaling(args.signaling_host, args.signaling_port)
    elif args.signaling == 'unix-socket':
        return UnixSocketSignaling(args.signaling_path)
    else:
        return CopyAndPasteSignaling()
