import asyncio

async def run():
    cmd="/anaconda3/envs/py37/bin/python3 /Users/shuyi.wang/Documents/others/aiortc/examples/tmp/stdout_same_line.py"
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE)

    while True:
        print("start reading")
        data = await proc.stdout.read(100)
        print("finished reading with length: {0}".format(len(data)))
        print(data)
        print("waiting")
        await asyncio.sleep(1.0)



asyncio.run(run())