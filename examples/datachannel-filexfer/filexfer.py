import argparse
import asyncio
import json
import logging

from aiortc import RTCPeerConnection, RTCSessionDescription


def channel_log(channel, t, message):
    print('channel(%s) %s %s' % (channel.label, t, message))


async def run_answer(pc, filename):
    done = asyncio.Event()

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            if message:
                fp.write(message)
            else:
                channel.send('done')
                done.set()

    # receive offer
    print('-- Please enter remote offer --')
    offer_json = json.loads(input())
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

    await done.wait()


async def run_offer(pc, fp):
    done = asyncio.Event()

    channel = pc.createDataChannel('filexfer')
    channel_log(channel, '-', 'created by local party')

    @channel.on('message')
    def on_message(message):
        # quit
        if message == 'done':
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

    # receive answer
    print('-- Please enter remote answer --')
    answer_json = json.loads(input())
    await pc.setRemoteDescription(RTCSessionDescription(
        sdp=answer_json['sdp'],
        type=answer_json['type']))
    print()

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
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    pc = RTCPeerConnection()
    if args.role == 'send':
        fp = open(args.filename, 'rb')
        coro = run_offer(pc, fp)
    else:
        fp = open(args.filename, 'wb')
        coro = run_answer(pc, fp)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        fp.close()
        loop.run_until_complete(pc.close())
