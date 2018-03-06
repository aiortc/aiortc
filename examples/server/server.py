import asyncio
import json
import logging
import os
import time
import wave

from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import (AudioFrame, AudioStreamTrack, VideoFrame,
                                 VideoStreamTrack)

PTIME = 0.02
ROOT = os.path.dirname(__file__)


async def pause(last):
    if last:
        now = time.time()
        await asyncio.sleep(last + PTIME - now)
    return time.time()


class AudioFileTrack(AudioStreamTrack):
    def __init__(self, path):
        self.last = None
        self.reader = wave.Wave_read(path)

    async def recv(self):
        self.last = await pause(self.last)
        return AudioFrame(
            channels=self.reader.getnchannels(),
            data=self.reader.readframes(160),
            sample_rate=self.reader.getframerate())


class VideoDummyTrack(VideoStreamTrack):
    def __init__(self):
        width = 640
        height = 480
        size = int(height * width * 12 / 8)

        self.counter = 0
        self.frame_green = VideoFrame(width=width, height=height, data=b'\x00' * size)
        self.frame_pink = VideoFrame(width=width, height=height, data=b'\xff' * size)
        self.last = None

    async def recv(self):
        self.last = await pause(self.last)
        self.counter += 1
        if (self.counter % 100) < 50:
            return self.frame_green
        else:
            return self.frame_pink


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

    @pc.on('track')
    def on_track(track):
        print("GOT TRACK", track.kind)

    await pc.setRemoteDescription(offer)
    pc.addTrack(AudioFileTrack(path=os.path.join(ROOT, 'demo-instruct.wav')))
    pc.addTrack(VideoDummyTrack())
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
