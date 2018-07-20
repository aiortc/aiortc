import argparse
import asyncio
import logging
import time

from aiortc import RTCPeerConnection
from signaling import add_signaling_arguments, create_signaling


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
                print('received %d bytes in %.1f s' % (octets, elapsed))
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
    channel = pc.createDataChannel('filexfer')

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

    # send file
    while True:
        data = fp.read(4096)
        channel.send(data)
        if not data:
            break

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
