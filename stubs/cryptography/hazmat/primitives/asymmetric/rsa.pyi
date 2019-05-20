from cryptography.hazmat.backends.interfaces import RSABackend
from cryptography.hazmat.primitives.asymmetric.padding import AsymmetricPadding
from cryptography.hazmat.primitives.hashes import HashAlgorithm
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    KeySerializationEncryption,
    PrivateFormat,
    PublicFormat,
)

class RSAPublicNumbers:
    n: int
    e: int
    def public_key(self, backend: RSABackend) -> RSAPublicKey: ...

class RSAPrivateNumbers:
    public_numbers: RSAPublicNumbers
    p: int
    q: int
    d: int
    dmp1: int
    dmq1: int
    iqmp: int
    def private_key(self, backend: RSABackend) -> RSAPrivateKey: ...

class RSAPublicKey:
    key_size: int
    def encrypt(self, plaintext: bytes, padding: AsymmetricPadding) -> bytes: ...
    def public_bytes(self, encoding: Encoding, format: PublicFormat) -> bytes: ...
    def public_numbers(self) -> RSAPublicNumbers: ...
    def sign(
        self, data: bytes, padding: AsymmetricPadding, algorithm: HashAlgorithm
    ) -> bytes: ...
    def verify(
        self,
        signature: bytes,
        data: bytes,
        padding: AsymmetricPadding,
        algorithm: HashAlgorithm,
    ) -> None: ...

class RSAPublicKeyWithSerialization(RSAPublicKey):
    pass

class RSAPrivateKey:
    key_size: int
    def decrypt(self, ciphertext: bytes, padding: AsymmetricPadding) -> bytes: ...
    def public_key(self) -> RSAPublicKey: ...
    def sign(
        self, data: bytes, padding: AsymmetricPadding, algorithm: HashAlgorithm
    ) -> bytes: ...

class RSAPrivateKeyWithSerialization(RSAPrivateKey):
    def private_numbers(self) -> RSAPrivateNumbers: ...
    def private_bytes(
        self,
        encoding: Encoding,
        format: PrivateFormat,
        encryption_algorithm: KeySerializationEncryption,
    ) -> bytes: ...

def generate_private_key(
    public_exponent: int, key_size: int, backend: RSABackend
) -> RSAPrivateKey: ...
