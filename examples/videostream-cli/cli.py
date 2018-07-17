import argparse
import asyncio
import logging
import math
import os

import cv2
import numpy

from aiortc import RTCPeerConnection
from aiortc.mediastreams import VideoFrame, VideoStreamTrack
from signaling import add_signaling_arguments, create_signaling

BLUE = (255, 0, 0)
GREEN = (0, 255, 0)
RED = (0, 0, 255)

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'output.png')


def frame_from_bgr(data_bgr):
    data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_YV12)
    return VideoFrame(width=data_bgr.shape[1], height=data_bgr.shape[0], data=data_yuv.tobytes())


def frame_to_bgr(frame):
    data_flat = numpy.frombuffer(frame.data, numpy.uint8)
    data_yuv = data_flat.reshape((math.ceil(frame.height * 12 / 8), frame.width))
    return cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_YV12)


class ColorVideoStreamTrack(VideoStreamTrack):
    def __init__(self, width, height, color):
        data_bgr = numpy.zeros((height, width, 3), numpy.uint8)
        data_bgr[:, :] = color
        self.frame = frame_from_bgr(data_bgr=data_bgr)

    async def recv(self):
        return self.frame


class CombinedVideoStreamTrack(VideoStreamTrack):
    def __init__(self, tracks):
        self.tracks = tracks

    async def recv(self):
        coros = [track.recv() for track in self.tracks]
        frames = await asyncio.gather(*coros)
        data_bgrs = [frame_to_bgr(frame) for frame in frames]
        data_bgr = numpy.hstack(data_bgrs)
        return frame_from_bgr(data_bgr)


async def run_answer(pc, signaling):
    remote_track = None

    @pc.on('track')
    def on_track(track):
        nonlocal remote_track
        assert track.kind == 'video'
        remote_track = track

    # receive offer
    offer = await signaling.receive()
    await pc.setRemoteDescription(offer)

    # send answer
    await pc.setLocalDescription(await pc.createAnswer())
    await signaling.send(pc.localDescription)

    print('Receiving video, press CTRL-C to stop')
    while True:
        frame = await remote_track.recv()
        data_bgr = frame_to_bgr(frame)
        cv2.imwrite(OUTPUT_PATH, data_bgr)


async def run_offer(pc, signaling):
    # add video track
    width = 320
    height = 240
    local_video = CombinedVideoStreamTrack(tracks=[
        ColorVideoStreamTrack(width=width, height=height, color=BLUE),
        ColorVideoStreamTrack(width=width, height=height, color=GREEN),
        ColorVideoStreamTrack(width=width, height=height, color=RED),
    ])
    pc.addTrack(local_video)

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    # receive answer
    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)

    print('Sending video for 10s')
    await asyncio.sleep(10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Video stream from the command line')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--verbose', '-v', action='count')
    add_signaling_arguments(parser)
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    signaling = create_signaling(args)
    pc = RTCPeerConnection()
    if args.role == 'offer':
        coro = run_offer(pc, signaling)
    else:
        coro = run_answer(pc, signaling)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
        loop.run_until_complete(signaling.close())
