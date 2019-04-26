import argparse
import asyncio
import logging
import time
import sys
import os

from os import path
sys.path.append(path.dirname(path.abspath(__file__)) + "/../../")

os.environ['AIORTC_SPECIAL_MODE'] = 'DC_ONLY'

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling

async def consume_signaling(pc, signaling):
    while True:
        obj = await signaling.receive()
        if isinstance(obj, RTCSessionDescription):
            print("recv remote sdp")
            await pc.setRemoteDescription(obj)
            if obj.type == 'offer': # run on receiver
                print("send local sdp (answer)")
                await pc.setLocalDescription(await pc.createAnswer())
                await signaling.send(pc.localDescription)
        else:
            print('Exiting')
            break


async def run_answer(pc, signaling, filename):
    await signaling.connect()

    @pc.on('datachannel')
    def on_datachannel(channel):
        start = time.time()
        octets = 0

        @channel.on('message')
        async def on_message(message):
            nonlocal octets

            if message:
                octets += len(message)
                fp.write(message)
            else:
                elapsed = time.time() - start
                print('received %d bytes in %.1f s (%.3f Mbps)' % (
                    octets, elapsed, octets * 8 / elapsed / 1000000))

                # say goodbye
                await signaling.send(None)

    await consume_signaling(pc, signaling)


async def run_offer(pc, signaling, fp):
    await signaling.connect()

    done_reading = False
    channel = pc.createDataChannel('filexfer')

    def send_data():
        nonlocal done_reading

        while (channel.bufferedAmount <= channel.bufferedAmountLowThreshold) and not done_reading:
            data = fp.read(16384)
            channel.send(data)
            if not data:
                done_reading = True

    channel.on('bufferedamountlow', send_data)
    channel.on('open', send_data)

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    print("send local sdp (offer)")
    await signaling.send(pc.localDescription)

    await consume_signaling(pc, signaling)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channel file transfer')
    parser.add_argument('role', choices=['send', 'receive'])
    parser.add_argument('filename')
    parser.add_argument('--verbose', '-v', action='count')
    add_signaling_arguments(parser)
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    #asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
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

    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        fp.close()
        loop.run_until_complete(pc.close())
        loop.run_until_complete(signaling.close())
