import argparse
import asyncio
import json
import logging

import requests
import websockets

from aiortc import (AudioStreamTrack, RTCPeerConnection, RTCSessionDescription,
                    VideoStreamTrack)
from aiortc.sdp import candidate_from_sdp


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

    async def send_description(self, description):
        message = json.dumps({
            'sdp': description.sdp,
            'type': description.type
        })
        print('>', message)
        await self.send({
            'cmd': 'send',
            'msg': message
        })


async def consume_signaling(signaling, pc, params):
    async def handle_message(message):
        print('<', message)
        if message['type'] == 'offer':
            await pc.setRemoteDescription(RTCSessionDescription(**message))
            await pc.setLocalDescription(await pc.createAnswer())
            await signaling.send_description(pc.localDescription)
        elif message['type'] == 'answer':
            await pc.setRemoteDescription(RTCSessionDescription(**message))
        elif message['type'] == 'candidate':
            candidate = candidate_from_sdp(message['candidate'].split(':', 1)[1])
            candidate.sdpMLineIndex = message['label']
            pc.addIceCandidate(candidate)

    for data in params['messages']:
        message = json.loads(data)
        await handle_message(message)
    while True:
        data = await signaling.recv()
        message = json.loads(data['msg'])
        await handle_message(message)


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
    response = requests.post('https://appr.tc/join/%s' % room)
    response.raise_for_status()
    data = response.json()
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
        await signaling.send_description(pc.localDescription)
        print('Please point a browser at %s' % params['room_link'])

    asyncio.ensure_future(consume_signaling(signaling, pc, params))

    # receive 60s of media
    await asyncio.sleep(60)

    # shutdown
    print('Shutting down')
    for c in consumers:
        c.cancel()
    await pc.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AppRTC')
    parser.add_argument('room')
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.get_event_loop().run_until_complete(join_room(args.room))
