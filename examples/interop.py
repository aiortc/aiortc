#
# !!! WARNING !!!
#
# This example uses some private APIs.
#

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from enum import Flag

from aioquic.asyncio import connect


class Result(Flag):
    V = 1
    H = 2
    D = 4
    C = 8
    R = 16
    Z = 32
    S = 64
    # M = 128
    # B = 256
    U = 512
    # 3 = 1024
    P = 2048

    def __str__(self):
        flags = sorted(
            map(
                lambda x: getattr(Result, x),
                filter(lambda x: not x.startswith("_"), dir(Result)),
            ),
            key=lambda x: x.value,
        )
        result_str = ""
        for flag in flags:
            if self & flag:
                result_str += flag.name
            else:
                result_str += "-"
        return result_str


@dataclass
class Config:
    name: str
    host: str
    port: int
    path: str
    result: Result = field(default_factory=lambda: Result(0))


CONFIGS = [
    Config("aioquic", "quic.aiortc.org", 4434, "/"),
    Config("ats", "quic.ogre.com", 4434, "/"),
    Config("f5", "208.85.208.226", 4433, "/"),
    Config("gquic", "quic.rocks", 4433, "/"),
    Config("lsquic", "http3-test.litespeedtech.com", 4434, None),
    Config("mvfst", "fb.mvfst.net", 4433, "/"),
    Config("ngtcp2", "nghttp2.org", 4434, None),
    Config("ngx_quic", "cloudflare-quic.com", 443, None),
    Config("pandora", "pandora.cm.in.tum.de", 4433, "/"),
    Config("picoquic", "test.privateoctopus.com", 4434, "/"),
    Config("quant", "quant.eggert.org", 4434, "/"),
    Config("quic-go", "quic.seemann.io", 443, "/"),
    Config("quiche", "quic.tech", 4433, "/"),
    Config("quicker", "quicker.edm.uhasselt.be", 4433, "/"),
    Config("quicly", "kazuhooku.com", 4434, "/"),
    Config("quinn", "ralith.com", 4433, "/"),
    Config("winquic", "quic.westus.cloudapp.azure.com", 4434, "/"),
]


async def http_request(connection, path):
    # perform HTTP/0.9 request
    reader, writer = await connection.create_stream()
    writer.write(("GET %s\r\n" % path).encode("utf8"))
    writer.write_eof()

    return await reader.read()


async def test_version_negotiation(config, **kwargs):
    async with connect(
        config.host, config.port, protocol_version=0x1A2A3A4A, **kwargs
    ) as connection:
        if connection._connection._version_negotiation_count == 1:
            config.result |= Result.V


async def test_handshake_and_close(config, **kwargs):
    async with connect(config.host, config.port, **kwargs) as connection:
        config.result |= Result.H
        if connection._connection._stateless_retry_count == 1:
            config.result |= Result.S
    config.result |= Result.C


async def test_data_transfer(config, **kwargs):
    if config.path is None:
        return

    async with connect(config.host, config.port, **kwargs) as connection:
        response1 = await http_request(connection, config.path)
        response2 = await http_request(connection, config.path)

        if response1 and response2:
            config.result |= Result.D


async def test_session_resumption(config, **kwargs):
    saved_ticket = None

    def session_ticket_handler(ticket):
        nonlocal saved_ticket
        saved_ticket = ticket

    # connect a first time, receive a ticket
    async with connect(
        config.host,
        config.port,
        session_ticket_handler=session_ticket_handler,
        **kwargs
    ) as connection:
        await connection.ping()

    # connect a second time, with the ticket
    if saved_ticket is not None:
        async with connect(
            config.host, config.port, session_ticket=saved_ticket, **kwargs
        ) as connection:
            await connection.ping()

        # check session was resumed
        if connection._connection.tls.session_resumed:
            config.result |= Result.R

        # check early data was accepted
        if connection._connection.tls.early_data_accepted:
            config.result |= Result.Z


async def test_key_update(config, **kwargs):
    async with connect(config.host, config.port, **kwargs) as connection:
        # cause some traffic
        await connection.ping()

        # request key update
        connection.request_key_update()

        # cause more traffic
        await connection.ping()

        config.result |= Result.U


async def test_spin_bit(config, **kwargs):
    async with connect(config.host, config.port, **kwargs) as connection:
        spin_bits = set()
        for i in range(5):
            await connection.ping()
            spin_bits.add(connection._connection._spin_bit_peer)
        if len(spin_bits) == 2:
            config.result |= Result.P


def print_result(config):
    print("%s%s%s" % (config.name, " " * (20 - len(config.name)), config.result))


async def run(only=None, **kwargs):
    configs = list(filter(lambda x: not only or x.name == only, CONFIGS))

    for config in configs:
        for test_name, test_func in filter(
            lambda x: x[0].startswith("test_"), globals().items()
        ):
            print("\n=== %s %s ===\n" % (config.name, test_name))
            try:
                await asyncio.wait_for(test_func(config, **kwargs), timeout=5)
            except Exception as exc:
                print(exc)
        print("")
        print_result(config)

    # print summary
    if len(configs) > 1:
        print("SUMMARY")
        for config in configs:
            print_result(config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC client")
    parser.add_argument("--only", type=str)
    parser.add_argument("--secrets-log-file", type=str)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.secrets_log_file:
        secrets_log_file = open(args.secrets_log_file, "a")
    else:
        secrets_log_file = None

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        run(alpn_protocols=["hq-20"], only=args.only, secrets_log_file=secrets_log_file)
    )
