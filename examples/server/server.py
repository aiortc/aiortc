import argparse
import asyncio
import json
import logging
import math
import os
import time
import wave

import cv2
import numpy
from aiohttp import web

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import (AudioFrame, AudioStreamTrack, VideoFrame,
                                 VideoStreamTrack)

ROOT = os.path.dirname(__file__)
AUDIO_OUTPUT_PATH = os.path.join(ROOT, 'output.wav')
AUDIO_PTIME = 0.020  # 20ms audio packetization


def frame_from_bgr(data_bgr):
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_YV12)
    return VideoFrame(width=data_bgr.shape[1], height=data_bgr.shape[0], data=data_yuv.tobytes())


def frame_from_gray(data_gray):
    data_bgr = cv2.cvtColor(data_gray, cv2.COLOR_GRAY2BGR)
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_YV12)
    return VideoFrame(width=data_bgr.shape[1], height=data_bgr.shape[0], data=data_yuv.tobytes())


def frame_to_bgr(frame):
    data_flat = numpy.frombuffer(frame.data, numpy.uint8)
    data_yuv = data_flat.reshape((math.ceil(frame.height * 12 / 8), frame.width))
    return cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_YV12)


class AudioFileTrack(AudioStreamTrack):
    def __init__(self, path):
        self.last = None
        self.reader = wave.open(path, 'rb')
        self.frames_per_packet = int(self.reader.getframerate() * AUDIO_PTIME)

    async def recv(self):
        # as we are reading audio from a file and not using a "live" source,
        # we need to control the rate at which audio is sent
        if self.last:
            now = time.time()
            await asyncio.sleep(self.last + AUDIO_PTIME - now)
        self.last = time.time()

        return AudioFrame(
            channels=self.reader.getnchannels(),
            data=self.reader.readframes(self.frames_per_packet),
            sample_rate=self.reader.getframerate())


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
    while True:
        frame = await track.recv()

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
