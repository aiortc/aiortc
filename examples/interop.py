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
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.logger import QuicLogger
from aioquic.quic.packet import QuicProtocolVersion


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
class Server:
    name: str
    host: str
    port: int = 4433
    retry_port: Optional[int] = 4434
    path: str = "/"
    result: Result = field(default_factory=lambda: Result(0))


SERVERS = [
    Server("aioquic", "quic.aiortc.org"),
    Server("ats", "quic.ogre.com"),
    Server("f5", "f5quic.com", retry_port=4433),
    Server("gquic", "quic.rocks", retry_port=None),
    Server("lsquic", "http3-test.litespeedtech.com"),
    Server("mvfst", "fb.mvfst.net"),
    Server("ngtcp2", "nghttp2.org"),
    Server("ngx_quic", "cloudflare-quic.com", port=443, retry_port=443),
    Server("pandora", "pandora.cm.in.tum.de"),
    Server("picoquic", "test.privateoctopus.com"),
    Server("quant", "quant.eggert.org"),
    Server("quic-go", "quic.seemann.io", port=443, retry_port=443),
    Server("quiche", "quic.tech", retry_port=4433),
    Server("quicker", "quicker.edm.uhasselt.be", retry_port=None),
    Server("quicly", "kazuhooku.com"),
    Server("quinn", "ralith.com"),
    Server("winquic", "quic.westus.cloudapp.azure.com"),
]


async def http_request(connection: QuicConnectionProtocol, path: str):
    # perform HTTP/0.9 request
    reader, writer = await connection.create_stream()
    writer.write(("GET %s\r\n" % path).encode("utf8"))
    writer.write_eof()

    return await reader.read()


async def http3_request(connection: QuicConnectionProtocol, authority: str, path: str):
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


async def test_version_negotiation(server: Server, configuration: QuicConfiguration):
    configuration.supported_versions = [0x1A2A3A4A, QuicProtocolVersion.DRAFT_22]

    async with connect(
        server.host, server.port, configuration=configuration
    ) as connection:
        await connection.ping()

        # check log
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if (
                category == "TRANSPORT"
                and event == "PACKET_RECEIVED"
                and data["type"] == "VERSION_NEGOTIATION"
            ):
                server.result |= Result.V


async def test_handshake_and_close(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as connection:
        await connection.ping()
        server.result |= Result.H
    server.result |= Result.C


async def test_stateless_retry(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.retry_port, configuration=configuration
    ) as connection:
        await connection.ping()

        # check log
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if (
                category == "TRANSPORT"
                and event == "PACKET_RECEIVED"
                and data["type"] == "RETRY"
            ):
                server.result |= Result.S


async def test_http_0(server: Server, configuration: QuicConfiguration):
    if server.path is None:
        return

    configuration.alpn_protocols = ["hq-22"]
    async with connect(
        server.host, server.port, configuration=configuration
    ) as connection:
        response = await http_request(connection, server.path)
        if response:
            server.result |= Result.D


async def test_http_3(server: Server, configuration: QuicConfiguration):
    if server.path is None:
        return

    configuration.alpn_protocols = ["h3-22"]
    async with connect(
        server.host, server.port, configuration=configuration
    ) as connection:
        response = await http3_request(connection, server.host, server.path)
        if response:
            server.result |= Result.D
            server.result |= Result.three


async def test_session_resumption(server: Server, configuration: QuicConfiguration):
    saved_ticket = None

    def session_ticket_handler(ticket):
        nonlocal saved_ticket
        saved_ticket = ticket

    # connect a first time, receive a ticket
    async with connect(
        server.host,
        server.port,
        configuration=configuration,
        session_ticket_handler=session_ticket_handler,
    ) as connection:
        await connection.ping()

    # connect a second time, with the ticket
    if saved_ticket is not None:
        configuration.session_ticket = saved_ticket
        async with connect(
            server.host, server.port, configuration=configuration
        ) as connection:
            await connection.ping()

            # check session was resumed
            if connection._quic.tls.session_resumed:
                server.result |= Result.R

            # check early data was accepted
            if connection._quic.tls.early_data_accepted:
                server.result |= Result.Z


async def test_key_update(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as connection:
        # cause some traffic
        await connection.ping()

        # request key update
        connection.request_key_update()

        # cause more traffic
        await connection.ping()

        server.result |= Result.U


async def test_spin_bit(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as connection:
        for i in range(5):
            await connection.ping()

        # check log
        spin_bits = set()
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if category == "CONNECTIVITY" and event == "SPIN_BIT_UPDATE":
                spin_bits.add(data["state"])
        if len(spin_bits) == 2:
            server.result |= Result.P


def print_result(server: Server) -> None:
    result = str(server.result).replace("three", "3")
    result = result[0:7] + " " + result[7:13] + " " + result[13:]
    print("%s%s%s" % (server.name, " " * (20 - len(server.name)), result))


async def run(servers, tests) -> None:
    for server in servers:
        for test_name, test_func in tests:
            print("\n=== %s %s ===\n" % (server.name, test_name))
            configuration = QuicConfiguration(
                alpn_protocols=["hq-22", "h3-22"],
                is_client=True,
                quic_logger=QuicLogger(),
            )
            try:
                await asyncio.wait_for(test_func(server, configuration), timeout=5)
            except Exception as exc:
                print(exc)
        print("")
        print_result(server)

    # print summary
    if len(servers) > 1:
        print("SUMMARY")
        for server in servers:
            print_result(server)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC interop client")
    parser.add_argument(
        "--server", type=str, help="only run against the specified server."
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
    servers = SERVERS
    tests = list(filter(lambda x: x[0].startswith("test_"), globals().items()))
    if args.server:
        servers = list(filter(lambda x: x.name == args.server, servers))
    if args.test:
        tests = list(filter(lambda x: x[0] == args.test, tests))

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run(servers=servers, tests=tests))
