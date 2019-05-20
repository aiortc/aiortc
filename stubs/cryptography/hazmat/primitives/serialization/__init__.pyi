from enum import Enum
from typing import Optional, Union

from cryptography.hazmat.backends.interfaces import PEMSerializationBackend
from cryptography.hazmat.primitives.asymmetric.dsa import DSAPrivateKey
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

class Encoding(Enum):
    PEM: str
    DER: str
    OpenSSH: str
    Raw: str
    X962: str

class PrivateFormat(Enum):
    PKCS8: str
    TraditionalOpenSSL: str
    Raw: str

class PublicFormat(Enum):
    SubjectPublicKeyInfo: str
    PKCS1: str
    OpenSSH: str
    Raw: str
    CompressedPoint: str
    UncompressedPoint: str

class KeySerializationEncryption: ...

class BestAvailableEncryption(KeySerializationEncryption):
    def __init__(self, password: bytes): ...

class NoEncryption(KeySerializationEncryption): ...

def load_pem_private_key(
    data: bytes, password: Optional[str], backend: PEMSerializationBackend
) -> Union[DSAPrivateKey, EllipticCurvePrivateKey, RSAPrivateKey]: ...
