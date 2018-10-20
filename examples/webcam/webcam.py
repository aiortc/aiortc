import argparse
import asyncio
import json
import logging
import os

from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer

ROOT = os.path.dirname(__file__)


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
    pcs.add(pc)

    @pc.on('iceconnectionstatechange')
    async def on_iceconnectionstatechange():
        print('ICE connection state is %s' % pc.iceConnectionState)
        if pc.iceConnectionState == 'failed':
            await pc.close()
            pcs.discard(pc)

    player = MediaPlayer('/dev/video0', options={'video_size': 'vga'})
    pc.addTrack(player.video)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))


pcs = set()


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WebRTC webcam demo')
    parser.add_argument('--port', type=int, default=8080,
                        help='Port for HTTP server (default: 8080)')
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get('/', index)
    app.router.add_get('/client.js', javascript)
    app.router.add_post('/offer', offer)
    web.run_app(app, port=args.port)
