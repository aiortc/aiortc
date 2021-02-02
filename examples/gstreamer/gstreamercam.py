import asyncio
import argparse
import json
import os
from collections import OrderedDict
from aiohttp import web
from h264track import H264EncodedStreamTrack
from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from aiortc.rtcrtpparameters import RTCRtpCodecCapability
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst


RATE = 15
ROOT = os.path.dirname(__file__)
camera = None

capabilities = RTCRtpSender.getCapabilities("video")
codec_parameters = OrderedDict(
    [
        ("packetization-mode", "1"),
        ("level-asymmetry-allowed", "1"),
        ("profile-level-id", "42001f"),
    ]
)
h264_capability = RTCRtpCodecCapability(
    mimeType="video/H264", clockRate=90000, channels=None, parameters=codec_parameters
)
preferences = [h264_capability]
rtsp_input = None
webcam_input = None


class GstH264Camera:
    WEBCAM_PIPELINE = "v4l2src device=/dev/{} ! video/x-h264,width=1280,height=720,framerate={}/1 ! queue ! appsink emit-signals=True name=h264_sink"
    RTSP_PIPELINE = "rtspsrc location={} latency=0 ! rtph264depay ! queue ! h264parse ! video/x-h264,alignment=nal,stream-format=byte-stream ! appsink emit-signals=True name=h264_sink"
    #RTSP_PIPELINE = "rtspsrc location={} latency=0 ! rtph264depay ! queue ! video/x-h264,alignment=nal,stream-format=byte-stream ! appsink emit-signals=True name=h264_sink"


    def __init__(self, rate, output, rtsp_input=None, webcam_input=None):
        if rtsp_input is not None and webcam_input is not None :
            raise Exception("Only one inupt can be used at once")
        if rtsp_input is None and webcam_input is None :
            raise Exception("Need to specify at least one input")
        if rtsp_input is not None:
            self.pipeline = Gst.parse_launch(GstH264Camera.RTSP_PIPELINE.format(rtsp_input))
        if webcam_input is not None:
            self.pipeline = Gst.parse_launch(GstH264Camera.WEBCAM_PIPELINE.format(webcam_input,RATE))
        self.output = output
        self.appsink = self.pipeline.get_by_name('h264_sink')
        self.appsink.connect("new-sample", self.on_buffer, None)
        self.pipeline.set_state(Gst.State.PLAYING)

    def on_buffer(self, sink, data) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if isinstance(sample, Gst.Sample):
            buffer = sample.get_buffer()
            byte_buffer = buffer.extract_dup(0, buffer.get_size())
            self.output.write(byte_buffer)
        return Gst.FlowReturn.OK

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)


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

    video_track = H264EncodedStreamTrack(RATE)
    camera = GstH264Camera(RATE, video_track, rtsp_input, webcam_input)

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
    global rtsp_input
    global webcam_input
    # close peer connections
    print("Shutting down")
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()
    camera.stop()
    del camera


if __name__ == "__main__":
    Gst.init(None)
    ap = argparse.ArgumentParser()
    ap.add_argument("-w", "--webcam", required=False,
                default=None, help="webcam input as videox")
    ap.add_argument("-s", "--stream", required=False,
                default=None, help="RTSP input as rtsp://...")

    args = vars(ap.parse_args())
    rtsp_input = args['stream']
    webcam_input = args['webcam']
    ssl_context = None
    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    web.run_app(app, host="0.0.0.0", port=8080, ssl_context=ssl_context)
