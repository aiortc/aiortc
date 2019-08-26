import asyncio
import datetime
import logging
import os

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def generate_ec_certificate(common_name, curve=ec.SECP256R1, alternative_names=[]):
    key = ec.generate_private_key(backend=default_backend(), curve=curve)

    subject = issuer = x509.Name(
        [x509.NameAttribute(x509.NameOID.COMMON_NAME, common_name)]
    )

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=10))
    )
    if alternative_names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(name) for name in alternative_names]
            ),
            critical=False,
        )
    cert = builder.sign(key, hashes.SHA256(), default_backend())
    return cert, key


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "rb") as fp:
        return fp.read()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


SERVER_CERTIFICATE = x509.load_pem_x509_certificate(
    load("ssl_cert.pem"), backend=default_backend()
)
SERVER_PRIVATE_KEY = serialization.load_pem_private_key(
    load("ssl_key.pem"), password=None, backend=default_backend()
)

if os.environ.get("AIOQUIC_DEBUG"):
    logging.basicConfig(level=logging.DEBUG)
