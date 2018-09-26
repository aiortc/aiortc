import argparse
import asyncio
import json
import logging
import os

import cv2
from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import (MediaBlackhole, MediaPlayer, MediaRecorder,
                                  video_frame_from_bgr, video_frame_from_gray,
                                  video_frame_to_bgr)

ROOT = os.path.dirname(__file__)


class VideoTransformTrack(VideoStreamTrack):
    def __init__(self, track, transform):
        self.counter = 0
        self.received = asyncio.Queue(maxsize=1)
        self.track = track
        self.transform = transform

    async def recv(self):
        frame = await self.track.recv()
        self.counter += 1

        # apply image processing to frame
        if self.transform == 'edges':
            img = video_frame_to_bgr(frame)
            edges = cv2.Canny(img, 100, 200)
            return video_frame_from_gray(edges, timestamp=frame.timestamp)
        elif self.transform == 'rotate':
            img = video_frame_to_bgr(frame)
            rows, cols, _ = img.shape
            M = cv2.getRotationMatrix2D((cols / 2, rows / 2), self.counter * 1.8, 1)
            rotated = cv2.warpAffine(img, M, (cols, rows))
            return video_frame_from_bgr(rotated, timestamp=frame.timestamp)
        else:
            return frame


async def index(request):
    content = open(os.path.join(ROOT, 'index.html'), 'r').read()
    return web.Response(content_type='text/html', text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, 'client.js'), 'r').read()
    return web.Response(content_type='application/javascript', text=content)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(
        sdp=params['sdp'],
        type=params['type'])

    pc = RTCPeerConnection()
    pcs.append(pc)

    # prepare local media
    player = MediaPlayer(path=os.path.join(ROOT, 'demo-instruct.wav'))
    if args.write_audio:
        recorder = MediaRecorder(path=args.write_audio)
    else:
        recorder = MediaBlackhole()

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            channel.send('pong')

    @pc.on('track')
    def on_track(track):
        print('Track %s received' % track.kind)

        if track.kind == 'audio':
            pc.addTrack(player.audio)
            recorder.addTrack(track)
        elif track.kind == 'video':
            local_video = VideoTransformTrack(track, transform=params['video_transform'])
            pc.addTrack(local_video)

        @track.on('ended')
        def on_ended():
            print('Track %s ended' % track.kind)
            recorder.stop()
            player.stop()

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    player.start()
    recorder.start()

    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))


pcs = []


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WebRTC audio / video / data-channels demo')
    parser.add_argument('--port', type=int, default=8080,
                        help='Port for HTTP server (default: 8080)')
    parser.add_argument('--verbose', '-v', action='count')
    parser.add_argument('--write-audio', help='Write received audio to a file')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get('/', index)
    app.router.add_get('/client.js', javascript)
    app.router.add_post('/offer', offer)
    web.run_app(app, port=args.port)
