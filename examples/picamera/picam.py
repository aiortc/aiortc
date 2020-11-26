import asyncio
import json
import os
from collections import OrderedDict

import picamera
from aiohttp import web
from pitrack import PiH264StreamTrack

from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from aiortc.rtcrtpparameters import RTCRtpCodecCapability

RATE = 30
ROOT = os.path.dirname(__file__)
camera = None

capabilities = RTCRtpSender.getCapabilities("video")
codec_parameters = OrderedDict([('packetization-mode', '1'),
                                ("level-asymmetry-allowed", "1"),
                                ('profile-level-id', '42001f')])
pi_capability = RTCRtpCodecCapability(mimeType='video/H264', clockRate=90000,
                                      channels=None,
                                      parameters=codec_parameters)
preferences = [pi_capability]


async def index(request):
    content = open(os.path.join(ROOT, "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    global camera
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    video_track = PiH264StreamTrack(RATE)
    camera = picamera.PiCamera()
    camera.resolution = (640, 480)
    camera.framerate = RATE
    camera.start_recording(video_track, format='h264', profile='constrained', inline_headers=True, sei=False)
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        print("ICE connection state is %s" % pc.iceConnectionState)
        if pc.iceConnectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer)
    for t in pc.getTransceivers():
        if t.kind == "video":
            t.setCodecPreferences(preferences)
            pc.addTrack(video_track)
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
    web.run_app(app, host='0.0.0.0', port=8080, ssl_context=ssl_context)
