import asyncio
import json
import os

import picamera
from aiohttp import web
from pitrack import PiH264StreamTrack

from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription

ROOT = os.path.dirname(__file__)
camera = None


def create_pi_track():
    global camera
    video_track = PiH264StreamTrack(30)
    camera = picamera.PiCamera()
    camera.resolution = (1280, 720)
    camera.framerate = 30
    target_bitrate = camera.resolution[0] * \
        camera.resolution[1] * \
        camera.framerate * 0.150
    camera.start_recording(
        video_track,
        format="h264",
        profile="constrained",
        bitrate=int(target_bitrate),  # From wowza recommended settings
        inline_headers=True,
        sei=False,
    )
    return video_track


async def index(request):
    content = open(os.path.join(ROOT, "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):

    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        print("ICE connection state is %s" % pc.iceConnectionState)
        if pc.iceConnectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    video = create_pi_track()

    transceiver = pc.addTransceiver("video")
    capabilities = RTCRtpSender.getCapabilities("video")
    preferences = list(filter(lambda x: x.name == "H264", capabilities.codecs))
    preferences += list(filter(lambda x: x.name == "rtx", capabilities.codecs))
    transceiver.setCodecPreferences(preferences)

    await pc.setRemoteDescription(offer)
    for t in pc.getTransceivers():
        if t.kind == "video":
            pc.addTrack(video)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    print(answer)
    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


pcs = set()


async def on_shutdown(app):
    global camera
    # close peer connections
    print("Shutting down")
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()
    camera.stop_recording()


if __name__ == "__main__":
    ssl_context = None
    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    web.run_app(app, host="0.0.0.0", port=8080, ssl_context=ssl_context)
