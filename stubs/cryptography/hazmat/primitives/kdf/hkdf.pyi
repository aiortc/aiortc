from cryptography.hazmat.backends.interfaces import HMACBackend
from cryptography.hazmat.primitives.hashes import HashAlgorithm

class HKDFExpand:
    def __init__(
        self, algorithm: HashAlgorithm, length: int, info: bytes, backend: HMACBackend
    ): ...
    def derive(self, key_material: bytes) -> bytes: ...
