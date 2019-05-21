import argparse
import asyncio
import logging

import aioquic


async def run(host, port, path, **kwargs):
    async with aioquic.connect(host, port, **kwargs) as connection:
        # perform HTTP/0.9 request
        reader, writer = await connection.create_stream()
        writer.write(("GET %s\r\n" % path).encode("utf8"))
        writer.write_eof()

        response = await reader.read()
        print(response.decode("utf8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QUIC client")
    parser.add_argument("host", type=str)
    parser.add_argument("port", type=int)
    parser.add_argument("--path", type=str, default="/")
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
            path=args.path,
            alpn_protocols=["hq-20"],
            secrets_log_file=secrets_log_file,
        )
    )
