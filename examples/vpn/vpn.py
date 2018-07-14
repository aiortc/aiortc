import argparse
import asyncio
import json
import logging
from aiortc import RTCPeerConnection, RTCSessionDescription

import tuntap


def channel_log(channel, t, message):
    print('channel(%s) %s %s' % (channel.label, t, repr(message)))


def create_pc():
    pc = RTCPeerConnection()

    @pc.on('datachannel')
    def on_datachannel(channel):
        channel_log(channel, '-', 'created by remote party')
    return pc


def tun_reader(tap, channel):
    def reader():
        print('-', end="\r")
        data = tap.fd.read(1500)
        if data:
            channel.send(data)
        print('+', end="\r")
    return reader


async def run_answer(pc, tap):
    done = asyncio.Event()

    @pc.on('datachannel')
    def on_datachannel(channel):
        tap.open()
        loop = asyncio.get_event_loop()
        loop.add_reader(tap.fd, tun_reader(tap, channel))

        @channel.on('message')
        def on_message(message):
            tap.fd.write(message)

        tap.up()

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


async def run_offer(pc, tap):
    done = asyncio.Event()

    channel = pc.createDataChannel('vpn')
    channel_log(channel, '-', 'created by local party')
    #channel_watch(channel)

    @channel.on('message')
    def on_message(message):
        tap.fd.write(message)

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

    tap.open()

    # send message
    loop = asyncio.get_event_loop()
    loop.add_reader(tap.fd, tun_reader(tap, channel))

    tap.up()

    await done.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Data channels with copy-and-paste signaling')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    tap = tuntap.Tun(name="revpn-%s" % args.role)

    pc = create_pc()
    if args.role == 'offer':
        coro = run_offer(pc, tap)
    else:
        coro = run_answer(pc, tap)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
