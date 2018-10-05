import argparse
import asyncio
import logging

import numpy
from av import VideoFrame

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole, MediaRecorder
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling


def create_rectangle(width, height, color):
    data_bgr = numpy.zeros((height, width, 3), numpy.uint8)
    data_bgr[:, :] = color
    return data_bgr


class FlagVideoStreamTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()  # don't forget this!
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


async def run(pc, signaling, recorder, role):
    @pc.on('track')
    def on_track(track):
        print('Receiving video')
        assert track.kind == 'video'
        recorder.addTrack(track)

    if role == 'offer':
        # send offer
        pc.addTrack(FlagVideoStreamTrack())
        await pc.setLocalDescription(await pc.createOffer())
        await signaling.send(pc.localDescription)

    # consume signaling
    while True:
        obj = await signaling.receive()

        if isinstance(obj, RTCSessionDescription):
            await pc.setRemoteDescription(obj)
            await recorder.start()

            if obj.type == 'offer':
                # send answer
                pc.addTrack(FlagVideoStreamTrack())
                await pc.setLocalDescription(await pc.createAnswer())
                await signaling.send(pc.localDescription)
        else:
            print('Exiting')
            break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Video stream from the command line')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--record-to', help='Write received media to a file.'),
    parser.add_argument('--verbose', '-v', action='count')
    add_signaling_arguments(parser)
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # create signaling and peer connection
    signaling = create_signaling(args)
    pc = RTCPeerConnection()

    # create media sink
    if args.record_to:
        recorder = MediaRecorder(args.record_to)
    else:
        recorder = MediaBlackhole()

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(run(
            pc=pc,
            recorder=recorder,
            role=args.role,
            signaling=signaling))
    except KeyboardInterrupt:
        pass
    finally:
        # cleanup
        loop.run_until_complete(recorder.stop())
        loop.run_until_complete(signaling.close())
        loop.run_until_complete(pc.close())
