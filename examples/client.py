import argparse
import asyncio
import logging
import pickle

import aioquic


def save_session_ticket(ticket):
    """
    Callback which is invoked by the TLS engine when a new session ticket
    is received.
    """
    print("New session ticket received")
    if args.session_ticket_file:
        with open(args.session_ticket_file, "wb") as fp:
            pickle.dump(ticket, fp)


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
    parser.add_argument("--session-ticket-file", type=str)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    if args.secrets_log_file:
        secrets_log_file = open(args.secrets_log_file, "a")
    else:
        secrets_log_file = None

    # load session ticket
    session_ticket = None
    if args.session_ticket_file:
        try:
            with open(args.session_ticket_file, "rb") as fp:
                session_ticket = pickle.load(fp)
        except FileNotFoundError:
            pass

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        run(
            host=args.host,
            port=args.port,
            path=args.path,
            alpn_protocols=["hq-20"],
            secrets_log_file=secrets_log_file,
            session_ticket=session_ticket,
            session_ticket_handler=save_session_ticket,
        )
    )
