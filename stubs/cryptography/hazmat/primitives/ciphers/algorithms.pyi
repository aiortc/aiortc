from cryptography.hazmat.primitives.ciphers import CipherAlgorithm

class AES(CipherAlgorithm):
    def __init__(self, key: bytes): ...

class ChaCha20(CipherAlgorithm):
    def __init__(self, key: bytes, nonce: bytes): ...
