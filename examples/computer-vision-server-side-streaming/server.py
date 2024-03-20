import asyncio
import ctypes
import json
import multiprocessing as mp
from time import sleep

import numpy as np
import shm
import websockets
from model import MyModel
from video import MyVideoCapture, SharedMemoryStreamTrack

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription


def frame_grabber(vi, frame_array):
    idx = 0
    while True:
        ok, bgr = vi.read()
        if not ok:
            break
        frame_array.set(idx, bgr)
        idx += 1


def model_runner(model, frame_array, inferences_array, render_array, save_render_or_inference):
    last_idx = None
    while True:
        # keep waiting until we get a new array:
        while True:
            idx, bgr = frame_array.get()
            if last_idx is None or idx != last_idx:
                bgr = bgr.copy()
                break
            sleep(0.001)
        # now run the model:
        circle = model.infer(bgr)
        # and save it:
        if save_render_or_inference == "inference":
            inferences_array.set(idx, circle)
        elif save_render_or_inference == "render":
            model.draw(bgr, circle)
            render_array.set(idx, bgr)
        else:
            raise KeyError(f"save_render_or_inference must be 'inference' or 'render' not '{save_render_or_inference}'")
        # update last_idx - now, i.e. only when we've completely gone through successfully
        last_idx = idx


def offer_factory(*, width, height, fps, frame_array, inferences_array):
    async def offer(websocket, *args, **kwargs):
        conf = RTCConfiguration()
        conf.iceServers = []  # do this to ensure no STUN servers (which are slow)
        pc = RTCPeerConnection(conf)
        track = SharedMemoryStreamTrack(frame_array=frame_array, fps=fps, height=height, width=width)

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            if pc.iceConnectionState == "failed":
                print("connection failed - stopping track")
                track.stop()

        @pc.on("negotiationneeded")
        async def on_negotiationneeded():
            print(pc)

        pc.addTrack(track)
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        # print("sending offer")
        offer = pc.localDescription
        offer = {"type": "rtc", "data": {"sdp": offer.sdp, "type": offer.type}}
        offer = json.dumps(offer, sort_keys=True)
        await websocket.send(offer)
        # print("waiting for answer")
        answer = await websocket.recv()
        # print("received answer")
        await pc.setRemoteDescription(RTCSessionDescription(**json.loads(answer)))
        # print("set answer")

        # send out the model results over the websocket as fast as we can get 'em:
        if inferences_array is not None:
            print("sending inferences up ...")
            last_idx = None
            while True:
                while True:
                    idx, circle = inferences_array.get()
                    if last_idx is None or idx != last_idx:
                        circle = circle.copy()
                        break
                    await asyncio.sleep(0.001)
                await websocket.send(json.dumps({"type": "circle", "data": circle.tolist()}))
                last_idx = idx

    return offer


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model", type=str, required=False, default="none", help="provide one of 'none', 'server', 'client'"
    )
    parser.add_argument(
        "--model-runtime",
        type=float,
        required=False,
        default=0.1,
        help="the time you want the model to take for each inference, in seconds",
    )
    parser.add_argument("--width", type=int, required=False, default=1920)
    parser.add_argument("--height", type=int, required=False, default=1080)
    parser.add_argument("--fps", type=int, required=False, default=25, help="fps of the simulated video")
    args = parser.parse_args()

    # set up our shared memory:
    h, w, c = args.height, args.width, 3
    frame_array = shm.IndexedArray(shape=(h, w, c), dtype=np.uint8, ctype=ctypes.c_byte)
    render_array = shm.IndexedArray(shape=(h, w, c), dtype=np.uint8, ctype=ctypes.c_byte)
    inferences_array = shm.IndexedArray(shape=(3,), dtype=np.float32, ctype=ctypes.c_float)

    model = args.model.lower()
    video_source_array = None
    run_model = None
    save_render_or_inference = None
    if args.model == "none":
        video_source_array = frame_array
        run_model = False
        save_render_or_inference = None
    elif args.model == "server":
        video_source_array = render_array
        run_model = True
        save_render_or_inference = "render"
    elif args.model == "client":
        video_source_array = frame_array
        run_model = True
        save_render_or_inference = "inference"
    else:
        raise KeyError(f"unknown model '{args.model}'")

    # video capture:
    vi = MyVideoCapture(fps=args.fps, width=w, height=h)
    frame_process = mp.Process(target=frame_grabber, args=(vi, frame_array))
    frame_process.start()

    # model:
    if run_model:
        model = MyModel(runtime=args.model_runtime)
        model_process = mp.Process(
            target=model_runner,
            kwargs=dict(
                model=model,
                frame_array=frame_array,
                inferences_array=inferences_array,
                render_array=render_array,
                save_render_or_inference=save_render_or_inference,
            ),
        )
        model_process.start()

    # websocket:
    start_server = websockets.serve(
        offer_factory(
            height=args.height,
            width=args.width,
            fps=args.fps,
            frame_array=video_source_array,
            inferences_array=inferences_array if run_model and save_render_or_inference == "inference" else None,
        ),
        "127.0.0.1",
        5678,
    )
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
