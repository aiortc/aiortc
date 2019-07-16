import argparse
import logging
import socket
import time
from urllib.parse import urlparse

from aioquic.configuration import QuicConfiguration
from aioquic.connection import QuicConnection
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, ResponseReceived

logger = logging.getLogger("http3")


def run(url: str) -> None:
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
            alpn_protocols=["h3-20"],
            is_client=True,
            secrets_log_file=open("/tmp/ssl.log", "w"),
            server_name=server_name,
        )
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
        for event in http.handle_events():
            print(event)
            if isinstance(event, (DataReceived, ResponseReceived)):
                stream_ended = event.stream_ended

        for data, addr in quic.datagrams_to_send(now=time.time()):
            sock.sendto(data, addr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HTTP/3 client")
    parser.add_argument("url", type=str, help="the server's host name or address")
    args = parser.parse_args()
    run(args.url)
