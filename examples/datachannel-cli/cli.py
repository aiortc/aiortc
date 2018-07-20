import argparse
import asyncio
import logging

from aiortc import RTCPeerConnection
from signaling import add_signaling_arguments, create_signaling


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


async def run_answer(pc, signaling):
    done = asyncio.Event()

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            # reply
            message = 'pong'
            channel_log(channel, '>', message)
            channel.send(message)

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
    done = asyncio.Event()

    channel = pc.createDataChannel('chat')
    channel_log(channel, '-', 'created by local party')
    channel_watch(channel)

    @channel.on('message')
    def on_message(message):
        # quit
        done.set()

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    # receive answer
    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)

    # send message
    message = 'ping'
    channel_log(channel, '>', message)
    channel.send(message)

    await done.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channels ping/pong')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--verbose', '-v', action='count')
    add_signaling_arguments(parser)

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    signaling = create_signaling(args)
    pc = create_pc()
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
