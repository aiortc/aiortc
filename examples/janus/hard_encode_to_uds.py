import av
import ffmpeg
import asyncio

# socket = "/tmp/ffmpeg4.socket"
named_pipe = "/tmp/fifo"

container = av.open(file="default:none", mode="r", format="avfoundation", options={"framerate": "30"})
frame_generator = container.decode(video=0)

encoder_process = (
    ffmpeg
        .input('pipe:', format='rawvideo', pix_fmt='uyvy422', s='{0}x{1}'.format(1280, 720))
        .output(named_pipe,
                **{"f": "h264", "c:v": "libx264", "preset": "ultrafast", "tune": "zerolatency", "pix_fmt": "yuv420p", "listen": "1"})
        .run_async(pipe_stdin=True)
)

p = (
    ffmpeg
        .input('pipe:0', format='rawvideo', pix_fmt='uyvy422', s='{0}x{1}'.format(1280, 720))
        .output("/tmp/fifo",
                **{"f": "h264", "c:v": "libx264", "preset": "ultrafast", "tune": "zerolatency"})
        .overwrite_output()
        .run_async(pipe_stdin=True)
)


FPS = 30
SLEEP_THRESHOLD = float(1) / (10 * FPS)
FRAME_TS_THRESHOLD = float(1) / (2 * FPS)


async def read_and_write():
    reader, writer = None, None

    async def read_loop(inner_reader):
        # read until empty
        while not inner_reader.at_eof():
            data = await inner_reader.read(10240)
            print("received: " + str(len(data)))

        print("yo yo yo")


    # for packet in container.demux((video_st,)):
    #     for frame in packet.decode():
    while True:
        # get next frame
        print("Getting frame")
        frame = next(frame_generator)
        len(frame.planes[0].to_bytes())

        # encode
        encoder_process.stdin.write(frame.planes[0].to_bytes())

        # wait
        await asyncio.sleep(FRAME_TS_THRESHOLD)

        if reader is None:
            reader, writer = await asyncio.open_unix_connection(socket)
            # reader.feed_eof()
            # read_loop_task = asyncio.create_task(read_loop(reader))
            # await read_loop_task

        print("Reading for frame", flush=True)

        # read until empty
        data = await reader.read(10240)  # this call blocks
        while True:
            print("received: " + str(len(data)))
            if len(data) < 8192:
                break
            data = await reader.read(10240)

        print("waiting for next frame")
        # wait
        await asyncio.sleep(SLEEP_THRESHOLD)


asyncio.run(read_and_write())
# print(SLEEP_THRESHOLD)
