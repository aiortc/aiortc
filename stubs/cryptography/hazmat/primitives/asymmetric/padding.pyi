from cryptography.hazmat.primitives.hashes import HashAlgorithm

class AsymmetricPadding:
    name: str

class MGF1:
    def __init__(self, algorithm: HashAlgorithm): ...

class PSS(AsymmetricPadding):
    def __init__(self, mgf: MGF1, salt_length: int): ...
