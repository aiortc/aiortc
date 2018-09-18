import argparse
import asyncio
import logging
import time

import uvloop

from aiortc import RTCPeerConnection
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling


def channel_log(channel, t, message):
    print('channel(%s) %s %s' % (channel.label, t, message))


def channel_send(channel, message):
    channel_log(channel, '>', message)
    channel.send(message)


async def run_answer(pc, signaling):
    done = asyncio.Event()

    @pc.on('datachannel')
    def on_datachannel(channel):
        channel_log(channel, '-', 'created by remote party')

        @channel.on('message')
        def on_message(message):
            channel_log(channel, '<', message)

            if message == 'ping':
                # reply
                channel_send(channel, 'pong')
            elif message == 'quit':
                # reply
                channel_send(channel, 'quit')

                # quit
                done.set()

    # receive offer
    offer = await signaling.receive()
    await pc.setRemoteDescription(offer)

    # send answer
    await pc.setLocalDescription(await pc.createAnswer())
    await signaling.send(pc.localDescription)

    await done.wait()


async def run_offer(pc, signaling):
    ready = asyncio.Event()
    done = asyncio.Event()

    channel = pc.createDataChannel('chat')
    channel_log(channel, '-', 'created by local party')

    @channel.on('open')
    def on_open():
        ready.set()

    @channel.on('message')
    def on_message(message):
        channel_log(channel, '<', message)

        if message == 'pong':
            elapsed_ms = (time.time() - start) * 1000
            print(' RTT %.2f ms' % elapsed_ms)
        if message == 'quit':
            done.set()

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    # receive answer
    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)

    # wait for channel to be ready
    await ready.wait()

    # send 10 pings
    for i in range(0, 10):
        start = time.time()
        channel_send(channel, 'ping')
        await asyncio.sleep(1)

    channel_send(channel, 'quit')
    await done.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channels ping/pong')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--verbose', '-v', action='count')
    add_signaling_arguments(parser)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
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
