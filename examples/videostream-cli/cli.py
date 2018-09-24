import argparse
import asyncio
import logging
import os

import cv2
import numpy

from aiortc import RTCPeerConnection, VideoStreamTrack
from aiortc.contrib.media import video_frame_from_bgr, video_frame_to_bgr
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'output.png')


async def consume_video(track):
    while True:
        frame = await track.recv()
        data_bgr = video_frame_to_bgr(frame)
        cv2.imwrite(OUTPUT_PATH, data_bgr)


def create_rectangle(color):
    data_bgr = numpy.zeros((480, 240, 3), numpy.uint8)
    data_bgr[:, :] = color
    return data_bgr


class FlagVideoStreamTrack(VideoStreamTrack):
    def __init__(self):
        self.data_bgr = numpy.hstack([
            create_rectangle((255, 0, 0)),      # blue
            create_rectangle((255, 255, 255)),  # white
            create_rectangle((0, 0, 255)),      # red
        ])

    async def recv(self):
        timestamp = await self.next_timestamp()
        return video_frame_from_bgr(self.data_bgr, timestamp=timestamp)


async def run_answer(pc, signaling):
    done = asyncio.Event()

    @pc.on('track')
    def on_track(track):
        print('Receiving video')
        assert track.kind == 'video'
        task = asyncio.ensure_future(consume_video(track))

        @track.on('ended')
        def on_ended():
            task.cancel()
            done.set()

    # receive offer
    offer = await signaling.receive()
    await pc.setRemoteDescription(offer)

    # send answer
    await pc.setLocalDescription(await pc.createAnswer())
    await signaling.send(pc.localDescription)

    # wait for completion
    await done.wait()


async def run_offer(pc, signaling):
    # add video track
    local_video = FlagVideoStreamTrack()
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
