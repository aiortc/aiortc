from unittest import TestCase

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from aioquic.connection import QuicConnection

from .utils import load

SERVER_CERTIFICATE = x509.load_pem_x509_certificate(
    load('ssl_cert.pem'), backend=default_backend())
SERVER_PRIVATE_KEY = serialization.load_pem_private_key(
    load('ssl_key.pem'), password=None, backend=default_backend())


class QuicConnectionTest(TestCase):
    def test_connect(self):
        client = QuicConnection(is_client=True)
        server = QuicConnection(
            is_client=False,
            certificate=SERVER_CERTIFICATE,
            private_key=SERVER_PRIVATE_KEY)

        # Initial (Client Hello + PADDING)
        client.connection_made()
        for datagram in client.pending_datagrams():
            server.datagram_received(datagram)
