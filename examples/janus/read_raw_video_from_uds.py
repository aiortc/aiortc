import asyncio
import time

async def client():
    reader, writer = await asyncio.open_unix_connection("/tmp/ffmpeg.socket")

    FPS = 30
    SLEEP_THRESHOLD = float(1) / (10 * FPS)
    FRAME_TS_THRESHOLD = float(1) / (2 * FPS)

    buffer = []
    last_ts = 0
    while True:
        data = await reader.read(102400)
        if len(data) > 0:
            ts = time.time()

            if ts - last_ts > FRAME_TS_THRESHOLD:  # check if this is next frame (interval larger than gap)
                # if this is new frame, put all data in the buffer to the output queue
                total_data_length = sum(map(lambda d: len(d), buffer))
                print("length: " + str(total_data_length) + " at " + str(len(buffer)) + " packages at ts " + str(ts))
                buffer.clear()

            # append the data to the buffer
            buffer.append(data)
            last_ts = ts
        else:
            # sleep to wait for data
            await asyncio.sleep(SLEEP_THRESHOLD)

asyncio.run(client())
