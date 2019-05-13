from typing import Union

from cryptography.hazmat.backends.interfaces import X509Backend
from cryptography.hazmat.primitives.asymmetric.dsa import DSAPublicKey
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.hashes import HashAlgorithm
from cryptography.hazmat.primitives.serialization import Encoding

class Certificate:
    serial_number: int
    def fingerprint(self, algorithm: HashAlgorithm) -> bytes: ...
    def public_bytes(self, encoding: Encoding) -> bytes: ...
    def public_key(
        self
    ) -> Union[DSAPublicKey, EllipticCurvePublicKey, RSAPublicKey]: ...

def load_der_x509_certificate(data: bytes, backend: X509Backend) -> Certificate: ...
def load_pem_x509_certificate(data: bytes, backend: X509Backend) -> Certificate: ...
