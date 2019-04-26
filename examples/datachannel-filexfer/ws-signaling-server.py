#!/usr/bin/env python
#
# Simple websocket server to perform signaling.
#

import asyncio
import binascii
import os

import websockets

clients = {}


async def echo(websocket, path):
    client_id = binascii.hexlify(os.urandom(8))
    clients[client_id] = websocket

    try:
        async for message in websocket:
            for c in clients.values():
                if c != websocket:
                    await c.send(message)
    finally:
        clients.pop(client_id)


asyncio.get_event_loop().run_until_complete(
    websockets.serve(echo, '0.0.0.0', 8765))
asyncio.get_event_loop().run_forever()
