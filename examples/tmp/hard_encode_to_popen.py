import asyncio
import os
import subprocess
import time
from threading import Thread

import av

named_pipe = "/tmp/fifo"

# container = av.open(file="default:none", mode="r", format="avfoundation", options={"framerate": "30"})  # Mac webcam
container = av.open(file="/dev/video0", mode="r", format="v4l2",  # linux camera
                    options={"framerate": "30", "input_format": "mjpeg", "video_size": "1920x1080"})

frame_generator = container.decode(video=0)

cmd = ["ffmpeg",
       "-y",
       "-nostdin",
       "-f", "rawvideo",
       "-framerate", "30",
       # "-pix_fmts", "yuv420p", # yuv420p or yuvj422p
       "-pixel_format", "rgb24",  # yuv420p or yuvj422p
       "-video_size", "1920x1080",
       "-i", "-",
       "-c:v", "libx264",
       "-f", "h264",
       # "-profile", "baseline",
       "-preset", "ultrafast",
       "-crf", "22",
       "-tune", "zerolatency",
       named_pipe]
       # "test.mkv"]

proc = subprocess.Popen(
    cmd,
    bufsize=0,
    stdout=subprocess.PIPE,
    stdin=subprocess.PIPE)

FPS = 30
SLEEP_THRESHOLD = float(1) / (10 * FPS)
FRAME_TS_THRESHOLD = float(1) / (2 * FPS)


global_data = b""


def read_named_pipe():
    global global_data
    print("try to open named pipe")
    pipe = os.open(named_pipe, os.O_RDONLY | os.O_NONBLOCK)  #  os.O_RDONLY | os.O_NONBLOCK
    while True:
        data = b""
        try:
            data = os.read(pipe, 100000000)
        except OSError as err:
            if err.errno == 11 or err.errno == 35:
                data = b""
            else:
                raise err
        if len(data) > 0:
            global_data += data
        time.sleep(SLEEP_THRESHOLD)


async def run():
    global buffer, proc, global_data

    # unblocking read of named pipe
    buffered_read_thread = Thread(target=read_named_pipe)
    buffered_read_thread.start()

    await asyncio.sleep(3.0)

    while True:
        print("Getting frame", flush=True)
        frame = next(frame_generator)
        proc.stdin.write(frame.to_image().tobytes())
        proc.stdin.flush()


        print("Reading", flush=True)
        old_data = global_data
        global_data = b""
        print("read data length: {0}".format(len(old_data)))

        await asyncio.sleep(SLEEP_THRESHOLD)


asyncio.run(run())
