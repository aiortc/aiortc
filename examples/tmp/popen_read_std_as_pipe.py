import subprocess
import asyncio
from threading import Thread

from bitstring import BitStream


buffer = BitStream()

# cmd = ["/anaconda3/envs/py37/bin/python3",
#        "/Users/shuyi.wang/Documents/others/aiortc/examples/tmp/stdout_same_line.py"]
cmd = ["/home/sdjksdafji/anaconda3/envs/py37/bin/python3",
       "/home/sdjksdafji/Documents/others/aiortc/examples/tmp/stdout_same_line.py"]
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stdin=subprocess.PIPE)


def buffered_read():
    global buffer, proc

    while True:
        data = proc.stdout.read(1)
        buffer.append(data)


async def run():
    global buffer, proc
    buffered_read_thread = Thread(target=buffered_read)
    buffered_read_thread.start()
    while True:
        print("Writing")
        proc.stdin.write(b"testtest")
        proc.stdin.flush()

        print("Reading")
        old_buffer = buffer
        buffer = BitStream()
        data = old_buffer.tobytes()
        print("read data length: {0}, data: {1}".format(len(data), data))
        print("Sleeping")
        await asyncio.sleep(1.0)


asyncio.run(run())