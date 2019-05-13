from enum import Enum

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
