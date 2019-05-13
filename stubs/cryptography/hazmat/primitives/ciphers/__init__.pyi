from typing import Union

from cryptography.hazmat.backends.interfaces import CipherBackend
from cryptography.hazmat.primitives.ciphers.modes import Mode

class CipherAlgorithm:
    key_size: int
    name: str

class CipherContext:
    def update(self, data: bytes) -> bytes: ...
    def update_into(self, data: bytes, buf: Union[bytearray, memoryview]) -> int: ...

class Cipher:
    def __init__(
        self, algorithm: CipherAlgorithm, mode: Mode, backend: CipherBackend
    ): ...
    def decryptor(self) -> CipherContext: ...
    def encryptor(self) -> CipherContext: ...
