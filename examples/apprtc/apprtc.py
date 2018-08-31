import argparse
import asyncio
import json
import logging
import os
import random
import time

import aiohttp
import cv2
import websockets
from aiortc import (AudioStreamTrack, RTCPeerConnection, RTCSessionDescription,
                    VideoStreamTrack)
from aiortc.contrib.media import frame_from_bgr
from aiortc.sdp import candidate_from_sdp

ROOT = os.path.dirname(__file__)
PHOTO_PATH = os.path.join(ROOT, 'photo.jpg')
VIDEO_PTIME = 1 / 30


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


class VideoImageTrack(VideoStreamTrack):
    def __init__(self):
        self.counter = 0
        self.img = cv2.imread(PHOTO_PATH, cv2.IMREAD_COLOR)
        self.last = None

    async def recv(self):
        # rotate image
        rows, cols, _ = self.img.shape
        M = cv2.getRotationMatrix2D((cols / 2, rows / 2), self.counter / 2, 1)
        rotated = cv2.warpAffine(self.img, M, (cols, rows))
        frame = frame_from_bgr(rotated)
        self.counter += 1

        # sleep
        if self.last:
            delta = self.last + VIDEO_PTIME - time.time()
            if delta > 0:
                await asyncio.sleep(delta)
        self.last = time.time()

        return frame


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
    pc.addTrack(VideoImageTrack())

    @pc.on('track')
    def on_track(track):
        print('Track %s received' % track.kind)

        if track.kind == 'audio':
            task = asyncio.ensure_future(consume_audio(track))
        elif track.kind == 'video':
            task = asyncio.ensure_future(consume_video(track))

        @track.on('ended')
        def on_ended():
            print('Track %s ended' % track.kind)
            task.cancel()

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
