import asyncio
import platform
import socketio

import cv2

from aiortc import (
    RTCIceCandidate,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.contrib.media import MediaPlayer
from aiortc.contrib.signaling import object_from_string, object_to_string

# Globals
sio = socketio.AsyncClient()
pc = None
is_initiator = False

def add_tracks():
    # Webcam to record from
    options = {"framerate": "30", "video_size": "640x360"}
    if platform.system() == "Darwin":
        webcam = MediaPlayer(
            "default:none", format="avfoundation", options=options
        )
    elif platform.system() == "Windows":
        webcam = MediaPlayer(
            "video=Integrated Camera", format="dshow", options=options
        )
    else:
        webcam = MediaPlayer("/dev/video0", format="v4l2", options=options)
    if webcam and webcam.audio:
        pc.addTrack(webcam.audio)

    if webcam and webcam.video:
        pc.addTrack(webcam.video)

async def createOffer():
    print("Creating an offer. Waiting for an answer")
    add_tracks()
    await pc.setLocalDescription(await pc.createOffer())
    await sio.emit("offer", object_to_string(pc.localDescription))

@sio.on("initiator")
async def on_initiator():
    global is_initiator
    is_initiator = True
    print("We are the initiator. Waiting for another client to join the room")

@sio.on("joined")
async def on_joined():
    print("Another client has joined the room")
    if is_initiator:
        await createOffer()

@sio.on("offer")
async def on_offer(data):
    print("Received an offer. Creating an answer")
    obj = object_from_string(data)
    if isinstance(obj, RTCSessionDescription):
        await pc.setRemoteDescription(obj)
        await pc.setLocalDescription(await pc.createAnswer())
        await sio.emit("answer", object_to_string(pc.localDescription))

@sio.on("answer")
async def on_answer(data):
    print("Received an answer. We should now be streaming video")
    obj = object_from_string(data)
    if isinstance(obj, RTCSessionDescription):
        await pc.setRemoteDescription(obj)

async def createWebRTCPeerConnection():
    global pc
    pc = RTCPeerConnection()
    
    @pc.on("track")
    async def on_remote_track(track):
        print("Receiving %s" % track.kind)
        if track.kind == "video":
            while True:
                try:
                    frame = await track.recv()
                    img = frame.to_rgb().to_ndarray()
                    cv2.imshow("Remote Video", img)
                    cv2.waitKey(5)
                except Exception as e:
                    print("Error receiving track", e)
                    raise e

async def main():
    await sio.connect("http://localhost:4000")
    await sio.emit("join")
    await createWebRTCPeerConnection()
    await sio.wait()

if __name__ == "__main__":
    # run event loop
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(
            main()
        )
    except KeyboardInterrupt:
        pass
    finally:
        # cleanup
        loop.run_until_complete(pc.close())
