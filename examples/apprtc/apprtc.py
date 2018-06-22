import argparse
import asyncio
import json
import logging
import random

import aiohttp
import websockets

from aiortc import (AudioStreamTrack, RTCPeerConnection, RTCSessionDescription,
                    VideoStreamTrack)
from aiortc.sdp import candidate_from_sdp


def description_to_dict(description):
    return {
        'sdp': description.sdp,
        'type': description.type
    }


class Signaling:
    async def connect(self, params):
        self.websocket = await websockets.connect(params['wss_url'], extra_headers={
            'Origin': 'https://appr.tc'
        })

    async def recv(self):
        data = await self.websocket.recv()
        return json.loads(data)

    async def send(self, data):
        await self.websocket.send(json.dumps(data))

    async def send_message(self, message):
        print('>', message)
        await self.send({
            'cmd': 'send',
            'msg': json.dumps(message)
        })


async def consume_signaling(signaling, pc, params):
    async def handle_message(message):
        print('<', message)

        if message['type'] == 'bye':
            return True

        if message['type'] == 'offer':
            await pc.setRemoteDescription(RTCSessionDescription(**message))
            await pc.setLocalDescription(await pc.createAnswer())
            await signaling.send_message(description_to_dict(pc.localDescription))
        elif message['type'] == 'answer':
            await pc.setRemoteDescription(RTCSessionDescription(**message))
        elif message['type'] == 'candidate':
            candidate = candidate_from_sdp(message['candidate'].split(':', 1)[1])
            candidate.sdpMid = message['id']
            candidate.sdpMLineIndex = message['label']
            pc.addIceCandidate(candidate)
        return False

    for data in params['messages']:
        message = json.loads(data)
        await handle_message(message)

    stop = False
    while not stop:
        data = await signaling.recv()
        message = json.loads(data['msg'])
        stop = await handle_message(message)


async def consume_audio(track):
    """
    Drain incoming audio.
    """
    while True:
        await track.recv()


async def consume_video(track):
    """
    Drain incoming video.
    """
    while True:
        await track.recv()


async def join_room(room):
    consumers = []

    # fetch room parameters
    async with aiohttp.ClientSession() as session:
        async with session.post('https://appr.tc/join/' + room) as response:
            # we cannot use response.json() due to:
            # https://github.com/webrtc/apprtc/issues/562
            data = json.loads(await response.text())
    assert data['result'] == 'SUCCESS'
    params = data['params']

    # create peer conection
    pc = RTCPeerConnection()
    pc.addTrack(AudioStreamTrack())
    pc.addTrack(VideoStreamTrack())

    @pc.on('track')
    def on_track(track):
        if track.kind == 'audio':
            consumers.append(asyncio.ensure_future(consume_audio(track)))
        elif track.kind == 'video':
            consumers.append(asyncio.ensure_future(consume_video(track)))

    # connect to websocket and join
    signaling = Signaling()
    await signaling.connect(params)
    await signaling.send({
        'clientid': params['client_id'],
        'cmd': 'register',
        'roomid': params['room_id'],
    })

    if params['is_initiator'] == 'true':
        # send offer
        await pc.setLocalDescription(await pc.createOffer())
        await signaling.send_message(description_to_dict(pc.localDescription))
        print('Please point a browser at %s' % params['room_link'])

    # receive 60s of media
    try:
        await asyncio.wait_for(consume_signaling(signaling, pc, params), timeout=60)
    except asyncio.TimeoutError:
        pass

    # shutdown
    print('Shutting down')
    await signaling.send_message({'type': 'bye'})
    for c in consumers:
        c.cancel()
    await pc.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AppRTC')
    parser.add_argument('room', nargs='?')
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if not args.room:
        args.room = ''.join([random.choice('0123456789') for x in range(10)])

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.get_event_loop().run_until_complete(join_room(args.room))
