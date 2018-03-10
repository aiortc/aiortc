import argparse
import asyncio
import json

from aiortc import RTCPeerConnection, RTCSessionDescription


async def run_answer():
    done = asyncio.Event()
    pc = RTCPeerConnection()

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            print('channel < %s' % message)

            # reply
            message = 'pong'
            print('channel > %s' % message)
            channel.send(message)

            # quit
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
    await pc.close()


async def run_offer():
    done = asyncio.Event()
    pc = RTCPeerConnection()

    channel = pc.createDataChannel('chat')

    @channel.on('message')
    def on_message(message):
        print('channel < %s' % message)

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

    # receive answer
    print('-- Please enter remote answer --')
    answer_json = json.loads(input())
    await pc.setRemoteDescription(RTCSessionDescription(
        sdp=answer_json['sdp'],
        type=answer_json['type']))
    print()

    # send message
    message = 'ping'
    print('channel > %s' % message)
    channel.send('ping')

    await done.wait()
    await pc.close()


parser = argparse.ArgumentParser(description='Data channels with copy-and-paste signaling')
parser.add_argument('role', choices=['offer', 'answer'])
args = parser.parse_args()

loop = asyncio.get_event_loop()
if args.role == 'offer':
    loop.run_until_complete(run_offer())
else:
    loop.run_until_complete(run_answer())
