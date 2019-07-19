import argparse
import logging
import pickle
import socket
import time
from urllib.parse import urlparse

from aioquic.configuration import QuicConfiguration
from aioquic.connection import QuicConnection
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, ResponseReceived

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


def run(url: str, **kwargs) -> None:
    # parse URL
    parsed = urlparse(url)
    assert parsed.scheme == "https", "Only HTTPS URLs are supported."
    if ":" in parsed.netloc:
        server_name, port_str = parsed.netloc.split(":")
        port = int(port_str)
    else:
        server_name = parsed.netloc
        port = 443

    # prepare socket
    server_addr = (socket.gethostbyname(server_name), port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # prepare QUIC connection
    quic = QuicConnection(
        configuration=QuicConfiguration(
            alpn_protocols=["h3-22"], is_client=True, server_name=server_name, **kwargs
        ),
        session_ticket_handler=save_session_ticket,
    )
    quic.connect(server_addr, now=time.time())

    # send request
    http = H3Connection(quic)
    stream_id = quic.get_next_available_stream_id()
    http.send_headers(
        stream_id=stream_id,
        headers=[
            (b":method", b"GET"),
            (b":scheme", parsed.scheme.encode("utf8")),
            (b":authority", parsed.netloc.encode("utf8")),
            (b":path", parsed.path.encode("utf8")),
        ],
    )
    http.send_data(stream_id=stream_id, data=b"", end_stream=True)
    for data, addr in quic.datagrams_to_send(now=time.time()):
        sock.sendto(data, addr)

    # handle events
    stream_ended = False
    while not stream_ended:
        data, addr = sock.recvfrom(2048)
        quic.receive_datagram(data, addr, now=time.time())

        # process events
        event = quic.next_event()
        while event is not None:
            for http_event in http.handle_event(event):
                print(http_event)
                if isinstance(http_event, (DataReceived, ResponseReceived)):
                    stream_ended = http_event.stream_ended
            event = quic.next_event()

        # send datagrams
        for data, addr in quic.datagrams_to_send(now=time.time()):
            sock.sendto(data, addr)

    # close connection
    quic.close()
    for data, addr in quic.datagrams_to_send(now=time.time()):
        sock.sendto(data, addr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP/3 client")
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

    run(url=args.url, secrets_log_file=secrets_log_file, session_ticket=session_ticket)
