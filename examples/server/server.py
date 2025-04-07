import argparse
import asyncio
import json
import logging
import os
import ssl
from collections import defaultdict

import cv2
from aiohttp import web
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder, MediaRelay
from aiortc.sdp import candidate_from_sdp
from av import VideoFrame

ROOT = os.path.dirname(__file__)


class IDWaiter:
    """
    A synchronization primitive that allows tasks to wait for a value
    associated with a specific ID to be set asynchronously.
    """

    def __init__(self):
        # Stores the values set for each ID
        self._values = {}

        # Stores asyncio.Event objects to notify waiters when a value is set
        self._events = defaultdict(asyncio.Event)

    def set(self, id, value):
        """
        Set a value for the given ID and notify any waiting tasks.

        Raises:
            RuntimeError: If a value has already been set for the ID.
        """
        if id in self._values:
            raise RuntimeError(f"Value for ID {id} already set.")
        self._values[id] = value
        self._events[id].set()

    async def get(self, id, timeout=None):
        """
        Wait for the value associated with the given ID.

        Args:
            id: The ID to wait for.
            timeout: Maximum time to wait in seconds (optional).

        Returns:
            The value associated with the ID.

        Raises:
            TimeoutError: If the value is not set within the timeout.
        """
        if id not in self._values:
            try:
                await asyncio.wait_for(self._events[id].wait(), timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(f"Timeout while waiting for ID {id}")
        return self._values[id]

    def delete(self, id):
        """
        Remove the stored value and event associated with the given ID.
        Does nothing if the ID does not exist.
        """
        self._values.pop(id, None)
        self._events.pop(id, None)


logger = logging.getLogger("pc")
pcs = IDWaiter()
relay = MediaRelay()


class VideoTransformTrack(MediaStreamTrack):
    """
    A video stream track that transforms frames from an another track.
    """

    kind = "video"

    def __init__(self, track, transform):
        super().__init__()  # don't forget this!
        self.track = track
        self.transform = transform

    async def recv(self):
        frame = await self.track.recv()

        if self.transform == "cartoon":
            img = frame.to_ndarray(format="bgr24")

            # prepare color
            img_color = cv2.pyrDown(cv2.pyrDown(img))
            for _ in range(6):
                img_color = cv2.bilateralFilter(img_color, 9, 9, 7)
            img_color = cv2.pyrUp(cv2.pyrUp(img_color))

            # prepare edges
            img_edges = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            img_edges = cv2.adaptiveThreshold(
                cv2.medianBlur(img_edges, 7),
                255,
                cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY,
                9,
                2,
            )
            img_edges = cv2.cvtColor(img_edges, cv2.COLOR_GRAY2RGB)

            # combine color and edges
            img = cv2.bitwise_and(img_color, img_edges)

            # rebuild a VideoFrame, preserving timing information
            new_frame = VideoFrame.from_ndarray(img, format="bgr24")
            new_frame.pts = frame.pts
            new_frame.time_base = frame.time_base
            return new_frame
        elif self.transform == "edges":
            # perform edge detection
            img = frame.to_ndarray(format="bgr24")
            img = cv2.cvtColor(cv2.Canny(img, 100, 200), cv2.COLOR_GRAY2BGR)

            # rebuild a VideoFrame, preserving timing information
            new_frame = VideoFrame.from_ndarray(img, format="bgr24")
            new_frame.pts = frame.pts
            new_frame.time_base = frame.time_base
            return new_frame
        elif self.transform == "rotate":
            # rotate image
            img = frame.to_ndarray(format="bgr24")
            rows, cols, _ = img.shape
            M = cv2.getRotationMatrix2D((cols / 2, rows / 2), frame.time * 45, 1)
            img = cv2.warpAffine(img, M, (cols, rows))

            # rebuild a VideoFrame, preserving timing information
            new_frame = VideoFrame.from_ndarray(img, format="bgr24")
            new_frame.pts = frame.pts
            new_frame.time_base = frame.time_base
            return new_frame
        else:
            return frame


async def index(request):
    content = open(os.path.join(ROOT, "index.html"), "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    content = open(os.path.join(ROOT, "client.js"), "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    params = await request.json()
    pc_id = request.match_info["pc_id"]
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

    pc = RTCPeerConnection(
        RTCConfiguration(iceServers=[RTCIceServer(urls="stun:stun.l.google.com:19302")])
    )

    def log_info(msg, *args):
        logger.info("PeerConnection(%s)" % pc_id + " " + msg, *args)

    log_info("Created for %s", request.remote)

    # prepare local media
    player = MediaPlayer(os.path.join(ROOT, "demo-instruct.wav"))
    if args.record_to:
        recorder = MediaRecorder(args.record_to)
    else:
        recorder = MediaBlackhole()

    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            if isinstance(message, str) and message.startswith("ping"):
                channel.send("pong" + message[4:])

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        log_info("Connection state is %s", pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.delete(pc_id)

    @pc.on("track")
    def on_track(track):
        log_info("Track %s received", track.kind)

        if track.kind == "audio":
            pc.addTrack(player.audio)
            recorder.addTrack(track)
        elif track.kind == "video":
            pc.addTrack(
                VideoTransformTrack(
                    relay.subscribe(track), transform=params["video_transform"]
                )
            )
            if args.record_to:
                recorder.addTrack(relay.subscribe(track))

        @track.on("ended")
        async def on_ended():
            log_info("Track %s ended", track.kind)
            await recorder.stop()

    # handle offer
    await pc.setRemoteDescription(offer)
    await recorder.start()

    # make `pc.addIceCandidate` able to be called
    # after `setRemoteDescription` for Trickle ICE
    pcs.set(pc_id, pc)

    # send answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.Response(
        content_type="application/json",
        text=json.dumps(
            {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
        ),
    )


async def add_candidate(request):
    params = await request.json()

    pc_id = request.match_info["pc_id"]

    # Wait until `pc.setRemoteDescription` is called in `offer` above
    # because it sets up the transceiver that `addIceCandidate` adds an ICE candidate to
    # while `add_candidate` can be called before it due to network jitter or something
    pc = await pcs.get(pc_id, timeout=10)

    candidate = candidate_from_sdp(params["candidate"])
    candidate.sdpMid = params.get("sdpMid")
    candidate.sdpMLineIndex = params.get("sdpMLineIndex")

    await pc.addIceCandidate(candidate)

    return web.Response()


async def on_shutdown(app):
    # close peer connections
    coros = [pc.close() for pc in pcs.values()]
    await asyncio.gather(*coros)
    pcs.clear()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="WebRTC audio / video / data-channels demo"
    )
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host for HTTP server (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8080, help="Port for HTTP server (default: 8080)"
    )
    parser.add_argument("--record-to", help="Write received media to a file.")
    parser.add_argument("--verbose", "-v", action="count")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.cert_file:
        ssl_context = ssl.SSLContext()
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    else:
        ssl_context = None

    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer/{pc_id}", offer)
    app.router.add_post("/add_candidate/{pc_id}", add_candidate)
    web.run_app(
        app, access_log=None, host=args.host, port=args.port, ssl_context=ssl_context
    )
