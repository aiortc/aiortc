from cryptography.hazmat.primitives.asymmetric.padding import AsymmetricPadding
from cryptography.hazmat.primitives.hashes import HashAlgorithm
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    KeySerializationEncryption,
    PrivateFormat,
    PublicFormat,
)


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
