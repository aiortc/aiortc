from cryptography.hazmat.primitives.serialization import (
    Encoding,
    KeySerializationEncryption,
    PrivateFormat,
    PublicFormat,
)

class X25519PublicKey:
    @classmethod
    def from_public_bytes(cls, data: bytes) -> X25519PublicKey: ...
    def public_bytes(self, encoding: Encoding, format: PublicFormat) -> bytes: ...

class X25519PrivateKey:
    @classmethod
    def from_private_bytes(cls, data: bytes) -> X25519PrivateKey: ...
    @classmethod
    def generate(cls) -> X25519PrivateKey: ...
    def exchange(self, peer_public_key: X25519PublicKey) -> bytes: ...
    def private_bytes(
        self,
        encoding: Encoding,
        format: PrivateFormat,
        encryption_algorithm: KeySerializationEncryption,
    ) -> bytes: ...
    def public_key(self) -> X25519PublicKey: ...
