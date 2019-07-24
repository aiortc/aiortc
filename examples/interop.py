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
from typing import Optional

from aioquic.asyncio import connect
from aioquic.h3.connection import H3Connection


class Result(Flag):
    V = 0x0001
    H = 0x0002
    D = 0x0004
    C = 0x0008
    R = 0x0010
    Z = 0x0020
    S = 0x0040
    M = 0x0080
    B = 0x0100
    U = 0x0200
    P = 0x0400
    E = 0x0800
    T = 0x1000
    three = 0x2000
    d = 0x4000
    p = 0x8000

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
    port: int = 4433
    retry_port: Optional[int] = 4434
    path: str = "/"
    result: Result = field(default_factory=lambda: Result(0))


CONFIGS = [
    Config("aioquic", "quic.aiortc.org"),
    Config("ats", "quic.ogre.com"),
    Config("f5", "f5quic.com", retry_port=4433),
    Config("gquic", "quic.rocks", retry_port=None),
    Config("lsquic", "http3-test.litespeedtech.com"),
    Config("mvfst", "fb.mvfst.net"),
    Config("ngtcp2", "nghttp2.org"),
    Config("ngx_quic", "cloudflare-quic.com", port=443, retry_port=443),
    Config("pandora", "pandora.cm.in.tum.de"),
    Config("picoquic", "test.privateoctopus.com"),
    Config("quant", "quant.eggert.org"),
    Config("quic-go", "quic.seemann.io", port=443, retry_port=443),
    Config("quiche", "quic.tech", retry_port=4433),
    Config("quicker", "quicker.edm.uhasselt.be", retry_port=None),
    Config("quicly", "kazuhooku.com"),
    Config("quinn", "ralith.com"),
    Config("winquic", "quic.westus.cloudapp.azure.com"),
]


async def http_request(connection, path):
    # perform HTTP/0.9 request
    reader, writer = await connection.create_stream()
    writer.write(("GET %s\r\n" % path).encode("utf8"))
    writer.write_eof()

    return await reader.read()


async def http3_request(connection, authority, path):
    reader, writer = await connection.create_stream()
    stream_id = writer.get_extra_info("stream_id")

    http = H3Connection(connection._quic)
    http.send_headers(
        stream_id=stream_id,
        headers=[
            (b":method", b"GET"),
            (b":scheme", b"https"),
            (b":authority", authority.encode("utf8")),
            (b":path", path.encode("utf8")),
        ],
    )
    http.send_data(stream_id=stream_id, data=b"", end_stream=True)

    return await reader.read()


async def test_version_negotiation(config, **kwargs):
    async with connect(
        config.host, config.port, protocol_version=0x1A2A3A4A, **kwargs
    ) as connection:
        await connection.ping()
        if connection._quic._version_negotiation_count == 1:
            config.result |= Result.V


async def test_handshake_and_close(config, **kwargs):
    async with connect(config.host, config.port, **kwargs) as connection:
        await connection.ping()
        config.result |= Result.H
    config.result |= Result.C


async def test_stateless_retry(config, **kwargs):
    async with connect(config.host, config.retry_port, **kwargs) as connection:
        await connection.ping()
        if connection._quic._stateless_retry_count == 1:
            config.result |= Result.S


async def test_http_0(config, **kwargs):
    if config.path is None:
        return

    kwargs["alpn_protocols"] = ["hq-22"]
    async with connect(config.host, config.port, **kwargs) as connection:
        response = await http_request(connection, config.path)
        if response:
            config.result |= Result.D


async def test_http_3(config, **kwargs):
    if config.path is None:
        return

    kwargs["alpn_protocols"] = ["h3-22"]
    async with connect(config.host, config.port, **kwargs) as connection:
        response = await http3_request(connection, config.host, config.path)
        if response:
            config.result |= Result.D
            config.result |= Result.three


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
            if connection._quic.tls.session_resumed:
                config.result |= Result.R

            # check early data was accepted
            if connection._quic.tls.early_data_accepted:
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
            spin_bits.add(connection._quic._spin_bit_peer)
        if len(spin_bits) == 2:
            config.result |= Result.P


def print_result(config: Config) -> None:
    result = str(config.result).replace("three", "3")
    result = result[0:7] + " " + result[7:13] + " " + result[13:]
    print("%s%s%s" % (config.name, " " * (20 - len(config.name)), result))


async def run(configs, tests, **kwargs) -> None:
    for config in configs:
        for test_name, test_func in tests:
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
    parser.add_argument(
        "--implementation",
        type=str,
        help="only test against the specified implementation.",
    )
    parser.add_argument("--test", type=str, help="only run the specifed test.")
    parser.add_argument(
        "-l",
        "--secrets-log",
        type=str,
        help="log secrets to a file, for use with Wireshark",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # open SSL log file
    if args.secrets_log:
        secrets_log_file = open(args.secrets_log, "a")
    else:
        secrets_log_file = None

    # determine what to run
    configs = CONFIGS
    tests = list(filter(lambda x: x[0].startswith("test_"), globals().items()))
    if args.implementation:
        configs = list(filter(lambda x: x.name == args.implementation, configs))
    if args.test:
        tests = list(filter(lambda x: x[0] == args.test, tests))

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        run(
            alpn_protocols=["hq-22", "h3-22"],
            configs=configs,
            tests=tests,
            secrets_log_file=secrets_log_file,
        )
    )
