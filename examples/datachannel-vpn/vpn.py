import argparse
import asyncio
import logging

import tuntap
from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.signaling import BYE, add_signaling_arguments, create_signaling

logger = logging.Logger("vpn")


def channel_log(channel, t, message):
    logger.info("channel(%s) %s %s" % (channel.label, t, repr(message)))


async def consume_signaling(pc, signaling):
    while True:
        obj = await signaling.receive()

        if isinstance(obj, RTCSessionDescription):
            await pc.setRemoteDescription(obj)

            if obj.type == "offer":
                # send answer
                await pc.setLocalDescription(await pc.createAnswer())
                await signaling.send(pc.localDescription)
        elif isinstance(obj, RTCIceCandidate):
            await pc.addIceCandidate(obj)
        elif obj is BYE:
            print("Exiting")
            break


def tun_start(tap, channel):
    tap.open()

    # relay channel -> tap
    channel.on("message")(tap.fd.write)

    # relay tap -> channel
    def tun_reader():
        data = tap.fd.read(tap.mtu)
        if data:
            channel.send(data)

    loop = asyncio.get_event_loop()
    loop.add_reader(tap.fd, tun_reader)

    tap.up()


async def run_answer(pc, signaling, tap):
    await signaling.connect()

    @pc.on("datachannel")
    def on_datachannel(channel):
        channel_log(channel, "-", "created by remote party")
        if channel.label == "vpntap":
            tun_start(tap, channel)

    await consume_signaling(pc, signaling)


async def run_offer(pc, signaling, tap):
    await signaling.connect()

    channel = pc.createDataChannel("vpntap")
    channel_log(channel, "-", "created by local party")

    @channel.on("open")
    def on_open():
        tun_start(tap, channel)

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    await signaling.send(pc.localDescription)

    await consume_signaling(pc, signaling)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VPN over data channel")
    parser.add_argument("role", choices=["offer", "answer"])
    parser.add_argument("--verbose", "-v", action="count")
    add_signaling_arguments(parser)
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    tap = tuntap.Tun(name="revpn-%s" % args.role)

    signaling = create_signaling(args)
    pc = RTCPeerConnection()
    if args.role == "offer":
        coro = run_offer(pc, signaling, tap)
    else:
        coro = run_answer(pc, signaling, tap)

    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
        loop.run_until_complete(signaling.close())
        tap.close()
