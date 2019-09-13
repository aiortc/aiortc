#
# !!! WARNING !!!
#
# This example uses some private APIs.
#

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Flag
from typing import Optional, cast

import requests
import urllib3
from http3_client import HttpClient

from aioquic.asyncio import connect
from aioquic.h3.events import DataReceived, HeadersReceived
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
    http3: bool = True
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
    Server("quant", "quant.eggert.org", http3=False),
    Server("quic-go", "quic.seemann.io", port=443, retry_port=443),
    Server("quiche", "quic.tech", port=8443, retry_port=4433),
    Server("quicker", "quicker.edm.uhasselt.be", retry_port=None),
    Server("quicly", "kazuhooku.com"),
    Server("quinn", "ralith.com"),
    Server("winquic", "quic.westus.cloudapp.azure.com"),
]


async def test_version_negotiation(server: Server, configuration: QuicConfiguration):
    configuration.supported_versions = [
        0x1A2A3A4A,
        QuicProtocolVersion.DRAFT_23,
        QuicProtocolVersion.DRAFT_22,
    ]

    async with connect(
        server.host, server.port, configuration=configuration
    ) as protocol:
        await protocol.ping()

        # check log
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if (
                category == "transport"
                and event == "packet_received"
                and data["packet_type"] == "version_negotiation"
            ):
                server.result |= Result.V


async def test_handshake_and_close(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as protocol:
        await protocol.ping()
        server.result |= Result.H
    server.result |= Result.C


async def test_stateless_retry(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.retry_port, configuration=configuration
    ) as protocol:
        await protocol.ping()

        # check log
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if (
                category == "transport"
                and event == "packet_received"
                and data["packet_type"] == "retry"
            ):
                server.result |= Result.S


async def test_http_0(server: Server, configuration: QuicConfiguration):
    if server.path is None:
        return

    configuration.alpn_protocols = ["hq-23", "hq-22"]
    async with connect(
        server.host,
        server.port,
        configuration=configuration,
        create_protocol=HttpClient,
    ) as protocol:
        protocol = cast(HttpClient, protocol)

        # perform HTTP request
        events = await protocol.get(
            "https://{}:{}{}".format(server.host, server.port, server.path)
        )
        if events and isinstance(events[0], HeadersReceived):
            server.result |= Result.D


async def test_http_3(server: Server, configuration: QuicConfiguration):
    if server.path is None:
        return

    configuration.alpn_protocols = ["h3-23", "h3-22"]
    async with connect(
        server.host,
        server.port,
        configuration=configuration,
        create_protocol=HttpClient,
    ) as protocol:
        protocol = cast(HttpClient, protocol)

        # perform HTTP request
        events = await protocol.get(
            "https://{}:{}{}".format(server.host, server.port, server.path)
        )
        if events and isinstance(events[0], HeadersReceived):
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
    ) as protocol:
        await protocol.ping()

    # connect a second time, with the ticket
    if saved_ticket is not None:
        configuration.session_ticket = saved_ticket
        async with connect(
            server.host, server.port, configuration=configuration
        ) as protocol:
            await protocol.ping()

            # check session was resumed
            if protocol._quic.tls.session_resumed:
                server.result |= Result.R

            # check early data was accepted
            if protocol._quic.tls.early_data_accepted:
                server.result |= Result.Z


async def test_key_update(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as protocol:
        # cause some traffic
        await protocol.ping()

        # request key update
        protocol.request_key_update()

        # cause more traffic
        await protocol.ping()

        server.result |= Result.U


async def test_migration(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as protocol:
        # cause some traffic
        await protocol.ping()

        # change connection ID and replace transport
        protocol.change_connection_id()
        protocol._transport.close()
        await loop.create_datagram_endpoint(lambda: protocol, local_addr=("::", 0))

        # cause more traffic
        await protocol.ping()

        # check log
        dcids = set()
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if (
                category == "transport"
                and event == "packet_received"
                and data["packet_type"] == "1RTT"
            ):
                dcids.add(data["header"]["dcid"])
        if len(dcids) == 2:
            server.result |= Result.M


async def test_rebinding(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as protocol:
        # cause some traffic
        await protocol.ping()

        # replace transport
        protocol._transport.close()
        await loop.create_datagram_endpoint(lambda: protocol, local_addr=("::", 0))

        # cause more traffic
        await protocol.ping()

        server.result |= Result.B


async def test_spin_bit(server: Server, configuration: QuicConfiguration):
    async with connect(
        server.host, server.port, configuration=configuration
    ) as protocol:
        for i in range(5):
            await protocol.ping()

        # check log
        spin_bits = set()
        for stamp, category, event, data in configuration.quic_logger.to_dict()[
            "traces"
        ][0]["events"]:
            if category == "connectivity" and event == "spin_bit_update":
                spin_bits.add(data["state"])
        if len(spin_bits) == 2:
            server.result |= Result.P


async def test_throughput(server: Server, configuration: QuicConfiguration):
    failures = 0

    for size in [5000000, 10000000]:
        print("Testing %d bytes download" % size)
        path = "/%d" % size

        # perform HTTP request over TCP
        start = time.time()
        response = requests.get("https://" + server.host + path, verify=False)
        tcp_octets = len(response.content)
        tcp_elapsed = time.time() - start
        assert tcp_octets == size, "HTTP/TCP response size mismatch"

        # perform HTTP request over QUIC
        if server.http3:
            configuration.alpn_protocols = ["h3-23", "h3-22"]
        else:
            configuration.alpn_protocols = ["hq-23", "hq-22"]
        start = time.time()
        async with connect(
            server.host,
            server.port,
            configuration=configuration,
            create_protocol=HttpClient,
        ) as protocol:
            protocol = cast(HttpClient, protocol)

            http_events = await protocol.get(
                "https://{}:{}{}".format(server.host, server.port, path)
            )
            quic_elapsed = time.time() - start
            quic_octets = 0
            for http_event in http_events:
                if isinstance(http_event, DataReceived):
                    quic_octets += len(http_event.data)
        assert quic_octets == size, "HTTP/QUIC response size mismatch"

        print(" - HTTP/TCP  completed in %.3f s" % tcp_elapsed)
        print(" - HTTP/QUIC completed in %.3f s" % quic_elapsed)

        if quic_elapsed > 1.1 * tcp_elapsed:
            failures += 1
            print(" => FAIL")
        else:
            print(" => PASS")

    if failures == 0:
        server.result |= Result.T


def print_result(server: Server) -> None:
    result = str(server.result).replace("three", "3")
    result = result[0:7] + " " + result[7:13] + " " + result[13:]
    print("%s%s%s" % (server.name, " " * (20 - len(server.name)), result))


async def run(servers, tests, quic_log=False, secrets_log_file=None) -> None:
    for server in servers:
        for test_name, test_func in tests:
            print("\n=== %s %s ===\n" % (server.name, test_name))
            configuration = QuicConfiguration(
                alpn_protocols=["h3-23", "h3-22", "hq-23", "hq-22"],
                is_client=True,
                quic_logger=QuicLogger(),
                secrets_log_file=secrets_log_file,
            )
            if test_name == "test_throughput":
                timeout = 60
            else:
                timeout = 5
            try:
                await asyncio.wait_for(
                    test_func(server, configuration), timeout=timeout
                )
            except Exception as exc:
                print(exc)

            if quic_log:
                with open("%s-%s.qlog" % (server.name, test_name), "w") as logger_fp:
                    json.dump(configuration.quic_logger.to_dict(), logger_fp, indent=4)

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
        "-q",
        "--quic-log",
        action="store_true",
        help="log QUIC events to a file in QLOG format",
    )
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
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="increase logging verbosity"
    )

    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

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

    # disable requests SSL warnings
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        run(
            servers=servers,
            tests=tests,
            quic_log=args.quic_log,
            secrets_log_file=secrets_log_file,
        )
    )
