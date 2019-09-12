#!/usr/bin/env python

import asyncio
import sys

from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration


async def http_client(host, port):
    configuration = QuicConfiguration(alpn_protocols=["hq-23"])

    async with connect(host, port, configuration=configuration) as connection:
        reader, writer = await connection.create_stream()
        writer.write(b"GET /\r\n")
        writer.write_eof()

        response = await reader.read()
        sys.stdout.buffer.write(response)

asyncio.get_event_loop().run_until_complete(
    http_client("quic.aiortc.org", 4433))
