import argparse
import asyncio
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Optional, Union

from aiortc import RTCIceCandidate, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp

logger = logging.getLogger(__name__)


class SignalingBye:
    pass


BYE = SignalingBye()

_SignalingObject = Union[RTCSessionDescription, RTCIceCandidate, SignalingBye]


def object_from_string(message_str: str) -> _SignalingObject:
    message = json.loads(message_str)
    if message["type"] in ["answer", "offer"]:
        return RTCSessionDescription(**message)
    elif message["type"] == "candidate" and message["candidate"]:
        candidate = candidate_from_sdp(message["candidate"].split(":", 1)[1])
        candidate.sdpMid = message["id"]
        candidate.sdpMLineIndex = message["label"]
        return candidate
    else:
        assert message["type"] == "bye"
        return BYE


def object_to_string(obj: _SignalingObject) -> str:
    message: dict[str, Union[int, str]]
    if isinstance(obj, RTCSessionDescription):
        message = {"sdp": obj.sdp, "type": obj.type}
    elif isinstance(obj, RTCIceCandidate):
        message = {
            "candidate": "candidate:" + candidate_to_sdp(obj),
            "id": obj.sdpMid,
            "label": obj.sdpMLineIndex,
            "type": "candidate",
        }
    else:
        assert obj is BYE
        message = {"type": "bye"}
    return json.dumps(message, sort_keys=True)


class BaseSignaling(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def send(self, descr: _SignalingObject) -> None: ...

    @abstractmethod
    async def receive(self) -> Optional[_SignalingObject]: ...


class CopyAndPasteSignaling(BaseSignaling):
    def __init__(self) -> None:
        self._read_pipe = sys.stdin
        self._read_transport: Optional[asyncio.ReadTransport] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._write_pipe = sys.stdout

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._reader = asyncio.StreamReader(loop=loop)
        self._read_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(self._reader), self._read_pipe
        )

    async def close(self) -> None:
        if self._reader is not None:
            await self.send(BYE)
            self._read_transport.close()
            self._reader = None

    async def receive(self) -> Optional[_SignalingObject]:
        print("-- Please enter a message from remote party --")
        data = await self._reader.readline()
        print()
        return object_from_string(data.decode(self._read_pipe.encoding))

    async def send(self, descr: _SignalingObject) -> None:
        print("-- Please send this message to the remote party --")
        self._write_pipe.write(object_to_string(descr) + "\n")
        self._write_pipe.flush()
        print()


class TcpSocketSignaling(BaseSignaling):
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: Optional[asyncio.Server] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        pass

    async def _connect(self, server: bool) -> None:
        if self._writer is not None:
            return

        if server:
            connected = asyncio.Event()

            def client_connected(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                self._reader = reader
                self._writer = writer
                connected.set()

            self._server = await asyncio.start_server(
                client_connected, host=self._host, port=self._port
            )
            await connected.wait()
        else:
            self._reader, self._writer = await asyncio.open_connection(
                host=self._host, port=self._port
            )

    async def close(self) -> None:
        if self._writer is not None:
            await self.send(BYE)
            self._writer.close()
            self._reader = None
            self._writer = None
        if self._server is not None:
            self._server.close()
            self._server = None

    async def receive(self) -> Optional[_SignalingObject]:
        await self._connect(False)
        try:
            data = await self._reader.readuntil()
        except asyncio.IncompleteReadError:
            return None
        return object_from_string(data.decode("utf8"))

    async def send(self, descr: _SignalingObject) -> None:
        await self._connect(True)
        data = object_to_string(descr).encode("utf8")
        self._writer.write(data + b"\n")


class UnixSocketSignaling(BaseSignaling):
    def __init__(self, path: str) -> None:
        self._path = path
        self._server: Optional[asyncio.Server] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> None:
        pass

    async def _connect(self, server: bool) -> None:
        if self._writer is not None:
            return

        if server:
            connected = asyncio.Event()

            def client_connected(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                self._reader = reader
                self._writer = writer
                connected.set()

            self._server = await asyncio.start_unix_server(
                client_connected, path=self._path
            )
            await connected.wait()
        else:
            self._reader, self._writer = await asyncio.open_unix_connection(self._path)

    async def close(self) -> None:
        if self._writer is not None:
            await self.send(BYE)
            self._writer.close()
            self._reader = None
            self._writer = None
        if self._server is not None:
            self._server.close()
            self._server = None
            # In Python 3.13, asyncio Unix sockets are removed when the server is
            # closed. On previous version we need to remove the socket ourselves.
            if sys.version_info < (3, 13):
                os.unlink(self._path)

    async def receive(self) -> Optional[_SignalingObject]:
        await self._connect(False)
        try:
            data = await self._reader.readuntil()
        except asyncio.IncompleteReadError:
            return None
        return object_from_string(data.decode("utf8"))

    async def send(self, descr: _SignalingObject) -> None:
        await self._connect(True)
        data = object_to_string(descr).encode("utf8")
        self._writer.write(data + b"\n")


def add_signaling_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add signaling method arguments to an argparse.ArgumentParser.
    """
    parser.add_argument(
        "--signaling",
        "-s",
        choices=["copy-and-paste", "tcp-socket", "unix-socket"],
    )
    parser.add_argument(
        "--signaling-host", default="127.0.0.1", help="Signaling host (tcp-socket only)"
    )
    parser.add_argument(
        "--signaling-port", default=1234, help="Signaling port (tcp-socket only)"
    )
    parser.add_argument(
        "--signaling-path",
        default="aiortc.socket",
        help="Signaling socket path (unix-socket only)",
    )


def create_signaling(args: argparse.Namespace) -> BaseSignaling:
    """
    Create a signaling method based on command-line arguments.
    """
    if args.signaling == "tcp-socket":
        return TcpSocketSignaling(args.signaling_host, args.signaling_port)
    elif args.signaling == "unix-socket":
        return UnixSocketSignaling(args.signaling_path)
    else:
        return CopyAndPasteSignaling()
