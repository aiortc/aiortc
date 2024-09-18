import argparse
import asyncio
import json
import logging
import os
import subprocess
import av
import threading
import av.packet
from multiprocessing import Event
from aiohttp import web
from aiortc.mediastreams import MediaStreamError
from aiortc.contrib.media import MediaRelay
from aiortc import (
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    MediaStreamTrack,
)
from aiortc.rtcrtpsender import RTCRtpSender

ROOT = os.path.dirname(__file__)

relay = None
track = None
worker_thread = None
term_event = Event()


class PacketStreamTrack(MediaStreamTrack):

    kind = "video"

    def __init__(self):
        super().__init__()
        self.queue = asyncio.Queue(maxsize=30)

    async def recv(self):
        packet = await self.queue.get()
        if packet is None:
            self.stop()
            raise MediaStreamError
        return packet


def run_remux_worker_ffmpeg(source, track, loop):
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-i",
            source,
            "-c",
            "copy",  # Copy codecs without re-encoding
            "-f",
            "mpegts",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=1000,
    )

    container = av.open(process.stdout, format="mpegts", mode="r", buffer_size=1000)
    for packet in container.demux(video=0):
        if packet.dts is not None:
            asyncio.run_coroutine_threadsafe(track.queue.put(packet), loop)

        if term_event.is_set():
            break

    process.terminate()
    container.close()
    asyncio.run_coroutine_threadsafe(track.queue.put(None), loop)


def create_remux_tracks(source: str):
    global relay, track, worker_thread

    if not worker_thread:
        relay = MediaRelay()
        track = PacketStreamTrack()
        worker_thread = threading.Thread(
            target=run_remux_worker_ffmpeg,
            args=(source, track, asyncio.get_event_loop()),
        )
        worker_thread.start()

    assert relay and track
    return relay.subscribe(track)


def force_codec(pc, sender, forced_codec):
    kind = forced_codec.split("/")[0]
    codecs = RTCRtpSender.getCapabilities(kind).codecs
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences(
        [codec for codec in codecs if codec.mimeType == forced_codec]
    )


async def index(request):
    content = open(os.path.join(ROOT, "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    video_track = create_remux_tracks(args.play_from)
    video_sender = pc.addTrack(video_track)
    force_codec(pc, video_sender, args.video_codec)
    await pc.setRemoteDescription(offer)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


pcs = set()


async def on_shutdown(app):
    term_event.set()

    # close peer connections
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC webcam demo")
    parser.add_argument(
        "--play-from",
        help="The media source, be a live stream transport address (e.g., RTSP url)",
    )
    parser.add_argument(
        "--video-codec", help="Force a specific video codec (e.g. video/H264)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for HTTP server (default: 8080)"
    )
    parser.add_argument("--verbose", "-v", action="count")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    web.run_app(app, host=args.host, port=args.port)
