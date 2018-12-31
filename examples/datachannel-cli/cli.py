import argparse
import asyncio
import logging
import time

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling


def channel_log(channel, t, message):
    print('channel(%s) %s %s' % (channel.label, t, message))


def channel_send(channel, message):
    channel_log(channel, '>', message)
    channel.send(message)


async def consume_signaling(pc, signaling):
    while True:
        obj = await signaling.receive()

        if isinstance(obj, RTCSessionDescription):
            await pc.setRemoteDescription(obj)

            if obj.type == 'offer':
                # send answer
                await pc.setLocalDescription(await pc.createAnswer())
                await signaling.send(pc.localDescription)
        else:
            print('Exiting')
            break


async def run_answer(pc, signaling):
    await signaling.connect()

    @pc.on('datachannel')
    def on_datachannel(channel):
        channel_log(channel, '-', 'created by remote party')

        @channel.on('message')
        def on_message(message):
            channel_log(channel, '<', message)

            if message == 'ping':
                # reply
                channel_send(channel, 'pong')

    await consume_signaling(pc, signaling)


async def run_offer(pc, signaling):
    await signaling.connect()

    channel = pc.createDataChannel('chat')
    channel_log(channel, '-', 'created by local party')
    start = None

    async def send_pings():
        nonlocal start

        while True:
            start = time.time()
            channel_send(channel, 'ping')
            await asyncio.sleep(1)

    @channel.on('open')
    def on_open():
        asyncio.ensure_future(send_pings())

    @channel.on('message')
    def on_message(message):
        channel_log(channel, '<', message)

        if message == 'pong':
            elapsed_ms = (time.time() - start) * 1000
            print(' RTT %.2f ms' % elapsed_ms)

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    await consume_signaling(pc, signaling)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channels ping/pong')
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
