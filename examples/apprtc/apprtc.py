import argparse
import asyncio
import json
import logging
import os
import random

import aiohttp
import cv2
import websockets
from av import VideoFrame

from aiortc import (AudioStreamTrack, RTCIceCandidate, RTCPeerConnection,
                    RTCSessionDescription, VideoStreamTrack)
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder
from aiortc.contrib.signaling import object_from_string, object_to_string

ROOT = os.path.dirname(__file__)
PHOTO_PATH = os.path.join(ROOT, 'photo.jpg')


class ApprtcSignaling:
    async def connect(self, room):
        # fetch room parameters
        async with aiohttp.ClientSession() as session:
            async with session.post('https://appr.tc/join/' + room) as response:
                # we cannot use response.json() due to:
                # https://github.com/webrtc/apprtc/issues/562
                data = json.loads(await response.text())
        assert data['result'] == 'SUCCESS'
        params = data['params']

        # join room
        self.websocket = await websockets.connect(params['wss_url'], extra_headers={
            'Origin': 'https://appr.tc'
        })
        await self.websocket.send(json.dumps({
            'clientid': params['client_id'],
            'cmd': 'register',
            'roomid': params['room_id'],
        }))
        self.__messages = params['messages']

        return params

    async def close(self):
        await self.send(None)
        self.websocket.close()

    async def receive(self):
        if self.__messages:
            message = self.__messages.pop(0)
        else:
            message = await self.websocket.recv()
            message = json.loads(message)['msg']
        print('<', message)
        return object_from_string(message)

    async def send(self, obj):
        message = object_to_string(obj)
        print('>', message)
        await self.websocket.send(json.dumps({
            'cmd': 'send',
            'msg': message,
        }))


class VideoImageTrack(VideoStreamTrack):
    """
    A video stream track that returns a rotating image.
    """
    def __init__(self):
        super().__init__()  # don't forget this!
        self.img = cv2.imread(PHOTO_PATH, cv2.IMREAD_COLOR)

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        # rotate image
        rows, cols, _ = self.img.shape
        M = cv2.getRotationMatrix2D((cols / 2, rows / 2), int(pts * time_base * 45), 1)
        img = cv2.warpAffine(self.img, M, (cols, rows))

        # create video frame
        frame = VideoFrame.from_ndarray(img, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base

        return frame


async def run(pc, player, recorder, room, signaling):
    def add_tracks():
        if player and player.audio:
            pc.addTrack(player.audio)
        else:
            pc.addTrack(AudioStreamTrack())

        if player and player.video:
            pc.addTrack(player.video)
        else:
            pc.addTrack(VideoImageTrack())

    @pc.on('track')
    def on_track(track):
        print('Track %s received' % track.kind)
        recorder.addTrack(track)

    # connect to websocket and join
    params = await signaling.connect(room)

    if params['is_initiator'] == 'true':
        # send offer
        add_tracks()
        await pc.setLocalDescription(await pc.createOffer())
        await signaling.send(pc.localDescription)
        print('Please point a browser at %s' % params['room_link'])

    # consume signaling
    while True:
        obj = await signaling.receive()

        if isinstance(obj, RTCSessionDescription):
            await pc.setRemoteDescription(obj)
            await recorder.start()

            if obj.type == 'offer':
                # send answer
                add_tracks()
                await pc.setLocalDescription(await pc.createAnswer())
                await signaling.send(pc.localDescription)
        elif isinstance(obj, RTCIceCandidate):
            pc.addIceCandidate(obj)
        else:
            print('Exiting')
            break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AppRTC')
    parser.add_argument('room', nargs='?')
    parser.add_argument('--play-from', help='Read the media from a file and sent it.'),
    parser.add_argument('--record-to', help='Write received media to a file.'),
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if not args.room:
        args.room = ''.join([random.choice('0123456789') for x in range(10)])

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # create signaling and peer connection
    signaling = ApprtcSignaling()
    pc = RTCPeerConnection()

    # create media source
    if args.play_from:
        player = MediaPlayer(args.play_from)
    else:
        player = None

    # create media sink
    if args.record_to:
        recorder = MediaRecorder(args.record_to)
    else:
        recorder = MediaBlackhole()

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run(
            pc=pc,
            player=player,
            recorder=recorder,
            room=args.room,
            signaling=signaling))
    except KeyboardInterrupt:
        pass
    finally:
        # cleanup
        loop.run_until_complete(recorder.stop())
        loop.run_until_complete(signaling.close())
        loop.run_until_complete(pc.close())
