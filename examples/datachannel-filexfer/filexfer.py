import argparse
import asyncio
import logging
import time

import uvloop

from aiortc import RTCPeerConnection
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling


async def run_answer(pc, signaling, filename):
    done = asyncio.Event()

    @pc.on('datachannel')
    def on_datachannel(channel):
        start = time.time()
        octets = 0

        @channel.on('message')
        def on_message(message):
            nonlocal octets

            if message:
                octets += len(message)
                fp.write(message)
            else:
                elapsed = time.time() - start
                print('received %d bytes in %.1f s (%.3f Mbps)' % (
                    octets, elapsed, octets * 8 / elapsed / 1000000))
                channel.send('done')
                done.set()

    # receive offer
    offer = await signaling.receive()
    await pc.setRemoteDescription(offer)

    # send answer
    await pc.setLocalDescription(await pc.createAnswer())
    await signaling.send(pc.localDescription)
    await done.wait()


async def run_offer(pc, signaling, fp):
    done = asyncio.Event()
    done_reading = False
    channel = pc.createDataChannel('filexfer')

    @channel.on('bufferedamountlow')
    def on_bufferedamountlow():
        nonlocal done_reading

        while (channel.bufferedAmount <= channel.bufferedAmountLowThreshold) and not done_reading:
            data = fp.read(16384)
            channel.send(data)
            if not data:
                done_reading = True

    @channel.on('message')
    def on_message(message):
        # quit
        if message == 'done':
            done.set()

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    # receive answer
    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)

    # start sending file
    on_bufferedamountlow()

    await done.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channel file transfer')
    parser.add_argument('role', choices=['send', 'receive'])
    parser.add_argument('filename')
    parser.add_argument('--verbose', '-v', action='count')
    add_signaling_arguments(parser)
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    signaling = create_signaling(args)
    pc = RTCPeerConnection()
    if args.role == 'send':
        fp = open(args.filename, 'rb')
        coro = run_offer(pc, signaling, fp)
    else:
        fp = open(args.filename, 'wb')
        coro = run_answer(pc, signaling, fp)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        fp.close()
        loop.run_until_complete(pc.close())
        loop.run_until_complete(signaling.close())
