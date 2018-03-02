import asyncio
import json
import logging
import os
import wave

from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioFrame, AudioStreamTrack

ROOT = os.path.dirname(__file__)


class AudioFileTrack(AudioStreamTrack):
    def __init__(self, path):
        self.reader = wave.Wave_read(path)

    async def recv(self):
        await asyncio.sleep(0.02)
        return AudioFrame(
            data=self.reader.readframes(160))


async def index(request):
    html = open(os.path.join(ROOT, 'index.html'), 'r').read()
    return web.Response(content_type='text/html', text=html)


async def offer(request):
    offer = await request.json()
    offer = RTCSessionDescription(
        sdp=offer['sdp'],
        type=offer['type'])

    pc = RTCPeerConnection()
    pcs.append(pc)

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            channel.send('pong')

    await pc.setRemoteDescription(offer)
    pc.addTrack(AudioFileTrack(path=os.path.join(ROOT, 'demo-instruct.wav')))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type='application/json',
        text=json.dumps({
            'sdp': pc.localDescription.sdp,
            'type': pc.localDescription.type
        }))


pcs = []


async def on_shutdown(app):
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)

logging.basicConfig(level=logging.DEBUG)
app = web.Application()
app.on_shutdown.append(on_shutdown)
app.router.add_get('/', index)
app.router.add_post('/offer', offer)
web.run_app(app, host='127.0.0.1', port=8080)
