import argparse
import asyncio
import logging
import os

import numpy
from av import VideoFrame

from aiortc import RTCPeerConnection, VideoStreamTrack
from aiortc.contrib.media import MediaRecorder
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), 'output-%3d.jpg')


def create_rectangle(width, height, color):
    data_bgr = numpy.zeros((height, width, 3), numpy.uint8)
    data_bgr[:, :] = color
    return data_bgr


class FlagVideoStreamTrack(VideoStreamTrack):
    def __init__(self):
        self.data_bgr = numpy.hstack([
            create_rectangle(width=213, height=480, color=(255, 0, 0)),      # blue
            create_rectangle(width=214, height=480, color=(255, 255, 255)),  # white
            create_rectangle(width=213, height=480, color=(0, 0, 255)),      # red
        ])

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        frame = VideoFrame.from_ndarray(self.data_bgr, format='bgr24')
        frame.pts = pts
        frame.time_base = time_base
        return frame


async def run_answer(pc, signaling):
    done = asyncio.Event()
    recorder = MediaRecorder(path=OUTPUT_PATH)

    @pc.on('track')
    def on_track(track):
        print('Receiving video')
        assert track.kind == 'video'
        recorder.addTrack(track)
        recorder.start()

        @track.on('ended')
        def on_ended():
            recorder.stop()
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
