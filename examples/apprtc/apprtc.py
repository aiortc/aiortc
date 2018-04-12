import argparse
import asyncio
import json

import requests
import websockets

from aiortc import (AudioStreamTrack, RTCPeerConnection, RTCSessionDescription,
                    VideoStreamTrack)


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
        print('got frame')


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
        print('Please point a browser at %s' % params['room_link'])

        # send offer
        await pc.setLocalDescription(await pc.createOffer())
        await signaling.send({
            'cmd': 'send',
            'msg': json.dumps({
                'sdp': pc.localDescription.sdp,
                'type': pc.localDescription.type
            })
        })

        # handle answer
        data = await signaling.recv()
        answer = json.loads(data['msg'])
        await pc.setRemoteDescription(RTCSessionDescription(**answer))
    else:
        # handle offer
        offer = json.loads(params['messages'][0])
        await pc.setRemoteDescription(RTCSessionDescription(**offer))

        # send answer
        await pc.setLocalDescription(await pc.createAnswer())
        await signaling.send({
            'cmd': 'send',
            'msg': json.dumps({
                'sdp': pc.localDescription.sdp,
                'type': pc.localDescription.type
            })
        })

    # receive 10s of media
    print('Receiving media')
    await asyncio.sleep(10)

    # shutdown
    print('Shutting down')
    for c in consumers:
        c.cancel()
    await pc.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AppRTC')
    parser.add_argument('room')
    args = parser.parse_args()

    asyncio.get_event_loop().run_until_complete(join_room(args.room))
