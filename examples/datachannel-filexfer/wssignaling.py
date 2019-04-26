import asyncio
import json
import os
import sys
import websockets

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

class WebsocketSignaling:
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._websocket = None

    async def connect(self):
        self._websocket = await websockets.connect("ws://" + str(self._host) + ":" + str(self._port))

    async def close(self):
        if self._websocket is not None and self._websocket.open is True:
            await self.send(None)
            await self._websocket.close()

    async def receive(self):
        try:
            data = await self._websocket.recv()
        except asyncio.IncompleteReadError: #TODO: replace to occur from websocket connection
            print("IncompleteReadError")
            return
        ret = object_from_string(data)
        if ret == None:
            print("remote host says good bye!")

        return ret

    async def send(self, descr):
        data = object_to_string(descr)
        await self._websocket.send(data + '\n')

def add_signaling_arguments(parser):
    """
    Add signaling method arguments to an argparse.ArgumentParser.
    """
    parser.add_argument('--signaling-host', default='127.0.0.1',
                        help='Signaling host (websocket server host)')
    parser.add_argument('--signaling-port', default=1234,
                        help='Signaling port (websocket server port)')


def create_signaling(args):
    """
    Create a signaling method based on command-line arguments.
    """
    return WebsocketSignaling(args.signaling_host, args.signaling_port)
