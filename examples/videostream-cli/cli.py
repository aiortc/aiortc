import argparse
import asyncio
import cv2
import json
import logging
import math
import numpy
import os

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import (VideoFrame, VideoStreamTrack)


_g_count = 10
_g_width = 320
_g_height = 240

_g_path_image = "{}/image.png".format(os.path.dirname(os.path.realpath(__file__)))
_g_path_offer = "{}/offer.json".format(os.path.dirname(os.path.realpath(__file__)))
_g_path_answer = "{}/answer.json".format(os.path.dirname(os.path.realpath(__file__)))


class VideoFrame_(VideoFrame):

    @classmethod
    def from_yuv(cls, height, width, data_yuv):
        data = data_yuv.tobytes()
        return cls(height=height, width=width, data=data)

    @classmethod
    def from_bgr(cls, height, width, data_bgr):
        data_yuv = cv2.cvtColor(data_bgr, cv2.COLOR_BGR2YUV_YV12)
        return cls.from_yuv(height=height, width=width, data_yuv=data_yuv)

    def to_yuv(self):
        # truncating the data as a workaround for #10
        data_len = math.ceil(self.height * 12 / 8 * self.width)
        data = self.data[0:data_len]
        data_flat = numpy.frombuffer(data, numpy.uint8)
        data_yuv = data_flat.reshape((math.ceil(self.height * 12 / 8), self.width))
        return data_yuv

    def to_bgr(self):
        data_yuv = self.to_yuv()
        data_bgr = cv2.cvtColor(data_yuv, cv2.COLOR_YUV2BGR_YV12)
        return data_bgr


class BlueVideoStreamTrack(VideoStreamTrack):

    def __init__(self, height=_g_height, width=_g_width):
        self.height = height
        self.width = width

    async def recv(self):
        data_bgr = numpy.zeros((self.height, self.width, 3), numpy.uint8)
        data_bgr[:, :] = (255, 0, 0)  # (B, G, R)
        return VideoFrame_.from_bgr(height=self.height, width=self.width, data_bgr=data_bgr)


class GreenVideoStreamTrack(VideoStreamTrack):

    def __init__(self, height=_g_height, width=_g_width):
        self.height = height
        self.width = width

    async def recv(self):
        data_bgr = numpy.zeros((self.height, self.width, 3), numpy.uint8)
        data_bgr[:, :] = (0, 255, 0)  # (B, G, R)
        return VideoFrame_.from_bgr(height=self.height, width=self.width, data_bgr=data_bgr)


class RedVideoStreamTrack(VideoStreamTrack):

    def __init__(self, height=_g_height, width=_g_width):
        self.height = height
        self.width = width

    async def recv(self):
        data_bgr = numpy.zeros((self.height, self.width, 3), numpy.uint8)
        data_bgr[:, :] = (0, 0, 255)  # (B, G, R)
        return VideoFrame_.from_bgr(height=self.height, width=self.width, data_bgr=data_bgr)


class CombinedVideoStreamTrack(VideoStreamTrack):

    def __init__(self, height=_g_height, width=_g_width, tracks=[None]):
        self.height = height
        self.width = width * len(tracks)
        # check tracks
        for track in tracks:
            assert(track is not None)
            assert(track.width == width)
            assert(track.height == height)
        self.tracks = tracks

    async def recv(self):
        frames = [await track.recv() for track in self.tracks]
        data_bgrs = [frame.to_bgr() for frame in frames]
        data_bgr = numpy.hstack(data_bgrs)
        return VideoFrame_.from_bgr(height=self.height, width=self.width, data_bgr=data_bgr)


async def consume_video(track, use_disk=False):
    while True:
        try:
            frame = await track.recv()
            frame = VideoFrame_(height=frame.height, width=frame.width, data=frame.data)
            data_bgr = frame.to_bgr()
            if use_disk:
                cv2.imwrite(_g_path_image, data_bgr)
        except Exception as e:
            print(e)


def channel_log(channel, t, message):
    print('channel(%s) %s %s' % (channel.label, t, message))


def channel_watch(channel):
    @channel.on('message')
    def on_message(message):
        channel_log(channel, '<', message)


def create_pc():
    pc = RTCPeerConnection()

    @pc.on('datachannel')
    def on_datachannel(channel):
        channel_log(channel, '-', 'created by remote party')
        channel_watch(channel)

    return pc


async def run_answer(pc, use_disk=False):
    done = asyncio.Event()

    _consumers = []

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            if 'quitting' in message:
                # quit
                message = 'quitting'
                channel_log(channel, '>', message)
                channel.send(message)
                done.set()
            else:
                # reply
                message = 'pong'
                channel_log(channel, '>', message)
                channel.send(message)

    @pc.on('track')
    def on_track(track):
        if track.kind == 'video':
            _consumers.append(asyncio.ensure_future(consume_video(track, use_disk)))

    # receive offer
    print('-- Please enter remote offer --')
    try:
        offer_json = json.loads(input())
    except json.decoder.JSONDecodeError as e:
        if use_disk:
            with open(_g_path_offer, 'r') as f:
                offer_json = json.loads(f.read())
        else:
            raise e
    await pc.setRemoteDescription(RTCSessionDescription(
        sdp=offer_json['sdp'],
        type=offer_json['type']))
    print()

    # send answer
    await pc.setLocalDescription(await pc.createAnswer())
    answer = pc.localDescription
    print('-- Your answer --')
    print(json.dumps({
        'sdp': answer.sdp,
        'type': answer.type
    }))
    print()
    if use_disk:
        with open(_g_path_answer, 'w') as f:
            f.write(json.dumps({
                'sdp': answer.sdp,
                'type': answer.type
            }))

    await done.wait()

    for c in _consumers:
        c.cancel()


async def run_offer(pc, use_disk=False):
    done = asyncio.Event()

    channel = pc.createDataChannel('chat')
    channel_log(channel, '-', 'created by local party')
    channel_watch(channel)

    # add video track
    local_video_red = RedVideoStreamTrack()
    local_video_green = GreenVideoStreamTrack()
    local_video_blue = BlueVideoStreamTrack()
    local_video = CombinedVideoStreamTrack(
        tracks=[local_video_red, local_video_green, local_video_blue])
    pc.addTrack(local_video)

    @channel.on('message')
    def on_message(message):
        if 'quitting' in message:
            # quit
            done.set()

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    offer = pc.localDescription
    print('-- Your offer --')
    print(json.dumps({
        'sdp': offer.sdp,
        'type': offer.type
    }))
    print()
    if use_disk:
        with open(_g_path_offer, 'w') as f:
            f.write(json.dumps({
                'sdp': offer.sdp,
                'type': offer.type
            }))

    # receive answer
    print('-- Please enter remote answer --')
    try:
        answer_json = json.loads(input())
    except json.decoder.JSONDecodeError as e:
        if use_disk:
            with open(_g_path_answer, 'r') as f:
                answer_json = json.loads(f.read())
        else:
            raise e
    await pc.setRemoteDescription(RTCSessionDescription(
        sdp=answer_json['sdp'],
        type=answer_json['type']))
    print()

    count = _g_count
    while count:
        count -= 1
        # send message
        message = 'ping'
        channel_log(channel, '>', message)
        channel.send(message)
        # sleep
        await asyncio.sleep(1)

    message = 'quitting'
    channel_log(channel, '>', message)
    channel.send(message)

    await done.wait()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channels with copy-and-paste signaling')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--disk', '-d', action='store_true')
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    pc = create_pc()
    if args.role == 'offer':
        coro = run_offer(pc, args.disk)
    else:
        coro = run_answer(pc, args.disk)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
