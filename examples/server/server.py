import argparse
import asyncio
import json
import logging
import os
import wave

import cv2
from aiohttp import web

from aiortc import (RTCPeerConnection, RTCSessionDescription, VideoFrame,
                    VideoStreamTrack)
from aiortc.contrib.media import (AudioFileTrack, frame_from_bgr,
                                  frame_from_gray, frame_to_bgr)

ROOT = os.path.dirname(__file__)
AUDIO_OUTPUT_PATH = os.path.join(ROOT, 'output.wav')


class VideoTransformTrack(VideoStreamTrack):
    def __init__(self, transform):
        self.counter = 0
        self.received = asyncio.Queue(maxsize=1)
        self.transform = transform

    async def recv(self):
        frame = await self.received.get()

        self.counter += 1
        if (self.counter % 100) > 50:
            # apply image processing to frame
            if self.transform == 'edges':
                img = frame_to_bgr(frame)
                edges = cv2.Canny(img, 100, 200)
                return frame_from_gray(edges)
            elif self.transform == 'rotate':
                img = frame_to_bgr(frame)
                rows, cols, _ = img.shape
                M = cv2.getRotationMatrix2D((cols / 2, rows / 2), self.counter * 7.2, 1)
                rotated = cv2.warpAffine(img, M, (cols, rows))
                return frame_from_bgr(rotated)
            elif self.transform == 'green':
                return VideoFrame(width=frame.width, height=frame.height)
            else:
                return frame
        else:
            # return raw frame
            return frame


async def consume_audio(track):
    """
    Drain incoming audio and write it to a file.
    """
    writer = None

    try:
        while True:
            frame = await track.recv()
            if writer is None:
                writer = wave.open(AUDIO_OUTPUT_PATH, 'wb')
                writer.setnchannels(frame.channels)
                writer.setframerate(frame.sample_rate)
                writer.setsampwidth(frame.sample_width)
            writer.writeframes(frame.data)
    finally:
        if writer is not None:
            writer.close()


async def consume_video(track, local_video):
    """
    Drain incoming video, and echo it back.
    """
    last_size = None

    while True:
        frame = await track.recv()

        # print frame size
        frame_size = (frame.width, frame.height)
        if frame_size != last_size:
            print('Received frame size', frame_size)
            last_size = frame_size

        # we are only interested in the latest frame
        if local_video.received.full():
            await local_video.received.get()

        await local_video.received.put(frame)


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
    pc._consumers = []
    pcs.append(pc)

    # prepare local media
    local_audio = AudioFileTrack(path=os.path.join(ROOT, 'demo-instruct.wav'))
    local_video = VideoTransformTrack(transform=params['video_transform'])

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            channel.send('pong')

    @pc.on('track')
    def on_track(track):
        if track.kind == 'audio':
            pc.addTrack(local_audio)
            pc._consumers.append(asyncio.ensure_future(consume_audio(track)))
        elif track.kind == 'video':
            pc.addTrack(local_video)
            pc._consumers.append(asyncio.ensure_future(consume_video(track, local_video)))

    await pc.setRemoteDescription(offer)
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
    # stop audio / video consumers
    for pc in pcs:
        for c in pc._consumers:
            c.cancel()

    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WebRTC audio / video / data-channels demo')
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
