import asyncio
import os
from struct import unpack


def random32():
    return unpack('!L', os.urandom(4))[0]


async def first_completed(*coros, timeout=None):
    tasks = [asyncio.ensure_future(x) for x in coros]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED,
                                           timeout=timeout)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    for task in pending:
        task.cancel()
    if len(done):
        return done.pop().result()
    else:
        raise TimeoutError
