import argparse
import asyncio
import logging
from enum import Flag

import aioquic


class Result(Flag):
    V = 1
    H = 2
    D = 4
    C = 8
    # R = 16
    # Z = 32
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


IMPLEMENTATIONS = [
    ("aioquic", "quic.aiortc.org", 4434, "/"),
    ("ats", "quic.ogre.com", 4434, "/"),
    ("f5", "208.85.208.226", 4433, "/"),
    ("lsquic", "http3-test.litespeedtech.com", 4434, None),
    ("mvfst", "fb.mvfst.net", 4433, "/"),
    ("ngtcp2", "nghttp2.org", 4434, None),
    ("ngx_quic", "cloudflare-quic.com", 443, None),
    ("picoquic", "test.privateoctopus.com", 4434, "/"),
    ("quant", "quant.eggert.org", 4434, "/"),
    ("quiche", "quic.tech", 4433, "/"),
    ("quicker", "quicker.edm.uhasselt.be", 4433, "/"),
    ("quicly", "kazuhooku.com", 4434, "/"),
    ("quinn", "ralith.com", 4433, "/"),
    ("winquic", "quic.westus.cloudapp.azure.com", 4434, "/"),
]


async def run_one(name, host, port, hq_path, **kwargs):
    result = Result(0)

    print("\n==== %s ====\n" % name)

    # version negotiation
    async with aioquic.connect(
        host, port, protocol_version=0x1A2A3A4A, **kwargs
    ) as connection:
        if connection._version_negotiation_count == 1:
            result |= Result.V

    # handshake + close
    async with aioquic.connect(host, port, **kwargs) as connection:
        result |= Result.H
        result |= Result.C
        if connection._stateless_retry_count == 1:
            result |= Result.S

    # data transfer
    if hq_path is not None:
        async with aioquic.connect(host, port, **kwargs) as connection:
            # perform HTTP/0.9 request
            reader, writer = await connection.create_stream()
            writer.write(("GET %s\r\n" % hq_path).encode("utf8"))
            writer.write_eof()

            response1 = await reader.read()

            # perform HTTP/0.9 request
            reader, writer = await connection.create_stream()
            writer.write(("GET %s\r\n" % hq_path).encode("utf8"))
            writer.write_eof()

            response2 = await reader.read()

            if response1 and response2:
                result |= Result.D

    # spin bit
    async with aioquic.connect(host, port, **kwargs) as connection:
        spin_bits = set()
        for i in range(4):
            reader, writer = await connection.create_stream()
            writer.write_eof()
            await asyncio.sleep(0.5)
            spin_bits.add(connection._spin_bit_peer)
        if len(spin_bits) == 2:
            result |= Result.P

    return result


async def run(only=None, **kwargs):
    results = []
    for name, host, port, path in IMPLEMENTATIONS:
        if not only or name == only:
            result = await run_one(name, host, port, path, **kwargs)
            results.append((name, result))
            print("\n%s%s%s" % (name, " " * (20 - len(name)), result))

    # print results
    print("")
    for name, result in results:
        print("%s%s%s" % (name, " " * (20 - len(name)), result))


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
