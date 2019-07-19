import argparse
import asyncio
import logging
import pickle
import time
from urllib.parse import urlparse

from aioquic.asyncio import connect

try:
    import uvloop
except ImportError:
    uvloop = None


logger = logging.getLogger("client")


def save_session_ticket(ticket):
    """
    Callback which is invoked by the TLS engine when a new session ticket
    is received.
    """
    logger.info("New session ticket received")
    if args.session_ticket:
        with open(args.session_ticket, "wb") as fp:
            pickle.dump(ticket, fp)


async def run(url, **kwargs) -> None:
    # parse URL
    parsed = urlparse(url)
    assert parsed.scheme == "https", "Only HTTPS URLs are supported."
    if ":" in parsed.netloc:
        server_name, port_str = parsed.netloc.split(":")
        port = int(port_str)
    else:
        server_name = parsed.netloc
        port = 443

    async with connect(server_name, port, **kwargs) as connection:
        # perform HTTP/0.9 request
        reader, writer = await connection.create_stream()
        writer.write(("GET %s\r\n" % parsed.path).encode("utf8"))
        writer.write_eof()

        start = time.time()
        response = await reader.read()
        elapsed = time.time() - start
        print(response.decode("utf8"))

        octets = len(response)
        logger.info(
            "Received %d bytes in %.1f s (%.3f Mbps)"
            % (octets, elapsed, octets * 8 / elapsed / 1000000)
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP/0.9 client")
    parser.add_argument("url", type=str, help="the URL to query (must be HTTPS)")
    parser.add_argument(
        "-l",
        "--secrets-log",
        type=str,
        help="log secrets to a file, for use with Wireshark",
    )
    parser.add_argument(
        "-s",
        "--session-ticket",
        type=str,
        help="read and write session ticket from the specified file",
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

    # load session ticket
    session_ticket = None
    if args.session_ticket:
        try:
            with open(args.session_ticket, "rb") as fp:
                session_ticket = pickle.load(fp)
        except FileNotFoundError:
            pass

    if uvloop is not None:
        uvloop.install()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        run(
            url=args.url,
            alpn_protocols=["hq-22"],
            secrets_log_file=secrets_log_file,
            session_ticket=session_ticket,
            session_ticket_handler=save_session_ticket,
        )
    )
