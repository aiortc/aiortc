import argparse
import asyncio
import logging
from aiortc import RTCPeerConnection
from signaling import CopyAndPasteSignaling
import tuntap
import functools

logger = logging.Logger('vpn')


def channel_log(channel, t, message):
    logger.info('channel(%s) %s %s' % (channel.label, t, repr(message)))


def create_pc():
    pc = RTCPeerConnection()

    @pc.on('datachannel')
    def on_datachannel(channel):
        channel_log(channel, '-', 'created by remote party')
    return pc


def tun_reader(channel, tap):
    data = tap.fd.read(tap.mtu)
    if data:
        channel.send(data)


def on_packet(tap, data):
    tap.fd.write(data)

async def run_answer(pc, tap):
    done = asyncio.Event()

    @pc.on('datachannel')
    def on_datachannel(channel):
        loop = asyncio.get_event_loop()
        if channel.label == 'vpntap':
            tap.open()
            loop.add_reader(
                tap.fd, functools.partial(tun_reader, channel, tap)
                )
            channel.on('message')(functools.partial(on_packet, tap))
            tap.up()

    # receive offer
    offer = await signaling.receive()
    await pc.setRemoteDescription(offer)

    # send answer
    await pc.setLocalDescription(await pc.createAnswer())
    await signaling.send(pc.localDescription)

    return done


async def run_offer(pc, tap):
    done = asyncio.Event()

    channel = pc.createDataChannel('vpntap')
    channel_log(channel, '-', 'created by local party')
    channel.on('message')(functools.partial(on_packet, tap))

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    # receive answer
    answer = await signaling.receive()
    await pc.setRemoteDescription(answer)

    tap.open()

    # connect tap to channel
    loop = asyncio.get_event_loop()
    loop.add_reader(tap.fd, functools.partial(tun_reader, channel, tap))

    tap.up()
    print('tap interface up')
    return done


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='VPN channels with copy-and-paste signaling')
    parser.add_argument('role', choices=['offer', 'answer'])
    parser.add_argument('--verbose', '-v', action='count')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    tap = tuntap.Tun(name="revpn-%s" % args.role)

    pc = create_pc()
    signaling = CopyAndPasteSignaling()
    if args.role == 'offer':
        coro = run_offer(pc, tap)
    else:
        coro = run_answer(pc, tap)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        done = loop.run_until_complete(coro)
        loop.run_until_complete(done.wait())
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
	tap.close()
