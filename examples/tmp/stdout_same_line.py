import asyncio
import random

async def run():
    while True:
        i = random.randint(1,99999)
        print(str(i), end="", flush=True)
        await asyncio.sleep(3.0)

asyncio.run(run())

