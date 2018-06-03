import asyncio
import os
import re
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

STUN_REGEX = '(?P<scheme>stun|stuns)\:(?P<host>[^?:]+)(\:(?P<port>[0-9]+?))?'
TURN_REGEX = ('(?P<scheme>turn|turns)\:(?P<host>[^?:]+)(\:(?P<port>[0-9]+?))?'
              '(\?transport=(?P<transport>.*))?')


def parse_stun_turn_uri(uri):
    if uri.startswith('stun'):
        match = re.fullmatch(STUN_REGEX, uri)
    elif uri.startswith('turn'):
        match = re.fullmatch(TURN_REGEX, uri)
    else:
        raise ValueError('malformed uri: invalid scheme')

    if not match:
        raise ValueError('malformed uri')

    match = match.groupdict()
    if match['port']:
        match['port'] = int(match['port'])

    return match
