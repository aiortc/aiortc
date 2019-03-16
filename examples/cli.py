import argparse
import asyncio
import ipaddress
import logging

from aioquic.connection import QuicConnection


class QuicProtocol(asyncio.DatagramProtocol):
    def __init__(self, secrets_log_file, server_name):
        self._connection = QuicConnection(secrets_log_file=secrets_log_file,
                                          server_name=server_name)
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport
        self._connection.connection_made()
        self._send_pending()

    def datagram_received(self, datagram, addr):
        self._connection.datagram_received(datagram)
        self._send_pending()

    def _send_pending(self):
        for datagram in self._connection.pending_datagrams():
            self._transport.sendto(datagram)


async def run(host, port, secrets_log_file):
    # if host is not an IP address, pass it to enable SNI
    try:
        ipaddress.ip_address(host)
        server_name = None
    except ValueError:
        server_name = host

    _, protocol = await loop.create_datagram_endpoint(
        lambda: QuicProtocol(secrets_log_file=secrets_log_file, server_name=server_name),
        remote_addr=(host, port))

    stream = protocol._connection.create_stream()
    stream.push_data(b'GET /\r\n')

    await asyncio.sleep(5)

    print(stream.pull_data())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='QUIC client')
    parser.add_argument('host', type=str)
    parser.add_argument('port', type=int)
    parser.add_argument('--secrets-log-file', type=str)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.secrets_log_file:
        secrets_log_file = open(args.secrets_log_file, 'a')
    else:
        secrets_log_file = None

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run(
        host=args.host,
        port=args.port,
        secrets_log_file=secrets_log_file))
