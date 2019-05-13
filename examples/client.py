import argparse
import asyncio
import ipaddress
import logging

from aioquic.connection import QuicConnection


async def run(host, port, **kwargs):
    # if host is not an IP address, pass it to enable SNI
    try:
        ipaddress.ip_address(host)
    except ValueError:
        kwargs["server_name"] = host

    _, protocol = await loop.create_datagram_endpoint(
        lambda: QuicConnection(is_client=True, **kwargs), remote_addr=(host, port)
    )
    await protocol.connect()

    stream = protocol.create_stream()
    stream.push_data(b"GET /\r\n")

    await asyncio.sleep(1)

    print(stream.pull_data())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC client")
    parser.add_argument("host", type=str)
    parser.add_argument("port", type=int)
    parser.add_argument("--secrets-log-file", type=str)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.secrets_log_file:
        secrets_log_file = open(args.secrets_log_file, "a")
    else:
        secrets_log_file = None

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        run(
            host=args.host,
            port=args.port,
            alpn_protocols=["http/0.9"],
            secrets_log_file=secrets_log_file,
        )
    )
