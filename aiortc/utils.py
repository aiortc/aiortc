import asyncio
import os
from struct import unpack


def random32():
    return unpack('!L', os.urandom(4))[0]


async def first_completed(*coros):
    tasks = [asyncio.ensure_future(x) for x in coros]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    for task in pending:
        task.cancel()
    return done.pop().result()
