from cryptography.hazmat.backends.interfaces import DSABackend
from cryptography.hazmat.primitives.asymmetric.padding import AsymmetricPadding
from cryptography.hazmat.primitives.hashes import HashAlgorithm
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    KeySerializationEncryption,
    PrivateFormat,
    PublicFormat,
)


class DSAParameters:
    ...


class DSAPublicNumbers:
    ...


class DSAPrivateNumbers:
    ...
    x: int
    public_numbers: DSAPublicNumbers

    def private_key(self, backend: DSABackend) -> DSAPrivateKey:
        ...


class DSAPublicKey:
    def public_bytes(self, encoding: Encoding, format: PublicFormat) -> bytes:
        ...

    def sign(
        self, data: bytes, padding: AsymmetricPadding, algorithm: HashAlgorithm
    ) -> bytes:
        ...

    def verify(
        self,
        signature: bytes,
        data: bytes,
        padding: AsymmetricPadding,
        algorithm: HashAlgorithm,
    ) -> None:
        ...


class DSAPrivateKey:
    key_size: int

    def parameters(self) -> DSAParameters:
        ...

    def public_key(self) -> DSAPublicKey:
        ...

    def sign(self, data: bytes, algorithm: HashAlgorithm) -> bytes:
        ...


def generate_parameters(key_size: int, backend: DSABackend) -> DSAParameters:
    ...


def generate_private_key(key_size: int, backend: DSABackend) -> DSAPrivateKey:
    ...
