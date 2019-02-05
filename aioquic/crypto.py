import binascii
import struct

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF, HKDFExpand

algorithm = hashes.SHA256()
backend = default_backend()

INITIAL_SALT = binascii.unhexlify('ef4fb0abb47470c41befcf8031334fae485e09a0')


def hkdf_label(label, length):
    full_label = b'tls13 ' + label
    return struct.pack('!HB', length, len(full_label)) + full_label + b'\x00'


def hkdf_expand_label(secret, label, length):
    return HKDFExpand(
        algorithm=algorithm,
        length=length,
        info=hkdf_label(label, length),
        backend=backend
    ).derive(secret)


def derive_keying_material(cid, is_client):
    if is_client:
        label = b'client in'
    else:
        label = b'server in'
    secret = HKDF(
        algorithm=algorithm,
        length=32,
        salt=INITIAL_SALT,
        info=hkdf_label(label, 32),
        backend=backend
    ).derive(cid)
    key = hkdf_expand_label(secret, b'quic key', 16)
    iv = hkdf_expand_label(secret, b'quic iv', 12)
    hp = hkdf_expand_label(secret, b'quic hp', 16)
    return key, iv, hp
