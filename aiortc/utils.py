import asyncio
import os
from struct import unpack


def random16():
    return unpack('!H', os.urandom(2))[0]


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
    for task in tasks:
        if task in done:
            return task.result()
    raise TimeoutError
