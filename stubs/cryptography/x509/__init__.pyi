import datetime
from typing import Any, Dict, List, Union

from cryptography.hazmat.backends.interfaces import X509Backend
from cryptography.hazmat.primitives.asymmetric.dsa import DSAPublicKey
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.hashes import HashAlgorithm
from cryptography.hazmat.primitives.serialization import Encoding

class ExtensionOID:
    SUBJECT_ALTERNATIVE_NAME: ExtensionOID

class Extension:
    oid: ExtensionOID
    value: str

class Certificate:
    extensions: List[Extension]
    not_valid_after: datetime.datetime
    not_valid_before: datetime.datetime
    serial_number: int
    subject: Dict
    _x509: Any
    def fingerprint(self, algorithm: HashAlgorithm) -> bytes: ...
    def public_bytes(self, encoding: Encoding) -> bytes: ...
    def public_key(
        self
    ) -> Union[DSAPublicKey, EllipticCurvePublicKey, RSAPublicKey]: ...

class DNSName:
    value: str

class NameOID:
    COMMON_NAME: NameOID

def load_der_x509_certificate(data: bytes, backend: X509Backend) -> Certificate: ...
def load_pem_x509_certificate(data: bytes, backend: X509Backend) -> Certificate: ...
