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
    Receive incoming audio.

    The audio can optionally be written to a file.
    """
    writer = None

    try:
        while True:
            frame = await track.recv()

            # write to file
            if args.write_audio:
                if writer is None:
                    writer = wave.open(args.write_audio, 'wb')
                    writer.setnchannels(frame.channels)
                    writer.setframerate(frame.sample_rate)
                    writer.setsampwidth(frame.sample_width)
                writer.writeframes(frame.data)
    finally:
        if writer is not None:
            writer.close()


async def consume_video(track, local_video):
    """
    Receive incoming video.

    The video can optionally be written to a file.
    """
    last_size = None
    writer = None

    try:
        while True:
            frame = await track.recv()

            # print frame size
            frame_size = (frame.width, frame.height)
            if frame_size != last_size:
                print('Received frame size', frame_size)
                last_size = frame_size

            # write to file
            if args.write_video:
                if writer is None:
                    fourcc = cv2.VideoWriter_fourcc(*'XVID')
                    writer = cv2.VideoWriter(args.write_video, fourcc, 30, frame_size)
                writer.write(frame_to_bgr(frame))

            # we are only interested in the latest frame
            if local_video.received.full():
                await local_video.received.get()

            await local_video.received.put(frame)
    finally:
        if writer is not None:
            writer.release()


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
    local_audio = AudioFileTrack(path=os.path.join(ROOT, 'demo-instruct.wav'))
    local_video = VideoTransformTrack(transform=params['video_transform'])

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            channel.send('pong')

    @pc.on('track')
    def on_track(track):
        print('Track %s received' % track.kind)

        if track.kind == 'audio':
            pc.addTrack(local_audio)
            task = asyncio.ensure_future(consume_audio(track))
        elif track.kind == 'video':
            pc.addTrack(local_video)
            task = asyncio.ensure_future(consume_video(track, local_video))

        @track.on('ended')
        def on_ended():
            print('Track %s ended' % track.kind)
            task.cancel()

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
    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WebRTC audio / video / data-channels demo')
    parser.add_argument('--port', type=int, default=8080,
                        help='Port for HTTP server (default: 8080)')
    parser.add_argument('--verbose', '-v', action='count')
    parser.add_argument('--write-audio', help='Write received audio to a file (WAV)')
    parser.add_argument('--write-video', help='Write received video to a file (AVI)')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get('/', index)
    app.router.add_get('/client.js', javascript)
    app.router.add_post('/offer', offer)
    web.run_app(app, port=args.port)
