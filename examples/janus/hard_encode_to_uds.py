import av
import ffmpeg
import asyncio
import os, errno, time

# socket = "/tmp/ffmpeg4.socket"
named_pipe = "/tmp/fifo"

# container = av.open(file="default:none", mode="r", format="avfoundation", options={"framerate": "30"})  # Mac webcam
container = av.open(file="/dev/video0", mode="r", format="v4l2",  # linux camera
                    options={"framerate": "30", "input_format": "mjpeg", "video_size": "1920x1080"})

frame_generator = container.decode(video=0)

encoder_process = (
    ffmpeg
        .input('pipe:', format='rawvideo', pix_fmt='yuvj422p', s='1920x1080')  # pix_fmt='uyvy422'
        .output(named_pipe,
                **{"f": "h264", "c:v": "libx264", "preset": "ultrafast", "tune": "zerolatency", "pix_fmt": "yuv420p"})  # , "listen": "1"
        .overwrite_output()
        .run_async(pipe_stdin=True)
)

# p = (
#     ffmpeg
#         .input('pipe:0', format='rawvideo', pix_fmt='uyvy422', s='{0}x{1}'.format(1280, 720))
#         .output("/tmp/fifo",
#                 **{"f": "h264", "c:v": "libx264", "preset": "ultrafast", "tune": "zerolatency"})
#         .overwrite_output()
#         .run_async(pipe_stdin=True)
# )


FPS = 30
SLEEP_THRESHOLD = float(1) / (10 * FPS)
FRAME_TS_THRESHOLD = float(1) / (2 * FPS)


def read_from_pipe(pipe):
    try:
        input = os.read(pipe, 102400)
        return input
    except OSError as err:
        if err.errno == 11 or err.errno == 35:
            return b""
        else:
            raise err


async def read_and_write():
    pipe = os.open(named_pipe, os.O_RDONLY | os.O_NONBLOCK)


    # for packet in container.demux((video_st,)):
    #     for frame in packet.decode():
    while True:
        # get next frame
        print("Getting frame", flush=True)
        frame = next(frame_generator)
        print("Got frame with length".format(len(frame.planes[0].to_bytes())), flush=True)

        # encode
        # encoder_process.stdin.write(frame.planes[0].to_bytes())
        for plane in frame.planes:
            encoder_process.stdin.write(plane.to_bytes())

        # wait
        await asyncio.sleep(FRAME_TS_THRESHOLD)

        print("Reading for frame", flush=True)

        # read until empty
        data = read_from_pipe(pipe)
        while data is not None and len(data) > 0:
            print("received: " + str(len(data)))
            data = read_from_pipe(pipe)

        print("waiting for next frame", flush=True)
        # wait
        await asyncio.sleep(SLEEP_THRESHOLD)


asyncio.run(read_and_write())
# print(SLEEP_THRESHOLD)
