import json
import logging
import os

from aiohttp import web
from aiowebrtc import RTCPeerConnection, RTCSessionDescription


ROOT = os.path.dirname(__file__)


async def index(request):
    html = open(os.path.join(ROOT, 'index.html'), 'r').read()
    return web.Response(content_type='text/html', text=html)


async def offer(request):
    offer = await request.json()
    offer = RTCSessionDescription(
        sdp=offer['sdp'],
        type=offer['type'])

    pc = RTCPeerConnection()
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))


logging.basicConfig(level=logging.DEBUG)
app = web.Application()
app.router.add_get('/', index)
app.router.add_post('/offer', offer)
web.run_app(app, host='127.0.0.1', port=8080)
