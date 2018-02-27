import asyncio


async def first_completed(*coros):
    tasks = [asyncio.ensure_future(x) for x in coros]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except asyncio.CancelledError:
        pending = tasks
        raise
    finally:
        for task in pending:
            task.cancel()
    return done.pop().result()
