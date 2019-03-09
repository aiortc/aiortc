import binascii

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import (Cipher, aead, algorithms,
                                                    modes)

from .packet import is_long_header
from .tls import hkdf_expand_label, hkdf_extract

INITIAL_SALT = binascii.unhexlify('ef4fb0abb47470c41befcf8031334fae485e09a0')
MAX_PN_SIZE = 4


def derive_initial_secret(cid, is_client):
    if is_client:
        label = b'client in'
    else:
        label = b'server in'

    algorithm = hashes.SHA256()
    initial_secret = hkdf_extract(algorithm, INITIAL_SALT, cid)
    secret = hkdf_expand_label(algorithm, initial_secret, label, b'', algorithm.digest_size)
    return algorithm, secret


def derive_key_iv_hp(algorithm, secret):
    return (
        hkdf_expand_label(algorithm, secret, b'quic key', b'', 16),
        hkdf_expand_label(algorithm, secret, b'quic iv', b'', 12),
        hkdf_expand_label(algorithm, secret, b'quic hp', b'', 16)
    )


class CryptoContext:
    def __init__(self):
        self.teardown()

    def decrypt_packet(self, packet, encrypted_offset):
        packet = bytearray(packet)

        # header protection
        sample_offset = encrypted_offset + MAX_PN_SIZE
        sample = packet[sample_offset:sample_offset + 16]
        encryptor = self.hp.encryptor()
        buf = bytearray(31)
        encryptor.update_into(sample, buf)
        mask = buf[:5]

        if is_long_header(packet[0]):
            # long header
            packet[0] ^= (mask[0] & 0x0f)
        else:
            # short header
            packet[0] ^= (mask[0] & 0x1f)

        pn_length = (packet[0] & 0x03) + 1
        for i in range(pn_length):
            packet[encrypted_offset + i] ^= mask[1 + i]
        pn = packet[encrypted_offset:encrypted_offset + pn_length]
        plain_header = bytes(packet[:encrypted_offset + pn_length])

        # payload protection
        nonce = bytearray(len(self.iv) - pn_length) + bytearray(pn)
        for i in range(len(self.iv)):
            nonce[i] ^= self.iv[i]
        payload = self.aead.decrypt(nonce, bytes(packet[encrypted_offset + pn_length:]),
                                    plain_header)

        # packet number
        packet_number = 0
        for i in range(pn_length):
            packet_number = (packet_number << 8) | pn[i]

        return plain_header, payload, packet_number

    def encrypt_packet(self, plain_header, plain_payload):
        pn_length = (plain_header[0] & 0x03) + 1
        pn_offset = len(plain_header) - pn_length
        pn = plain_header[pn_offset:pn_offset + pn_length]

        # payload protection
        nonce = bytearray(len(self.iv) - pn_length) + bytearray(pn)
        for i in range(len(self.iv)):
            nonce[i] ^= self.iv[i]
        protected_payload = self.aead.encrypt(nonce, plain_payload, plain_header)

        # header protection
        sample_offset = MAX_PN_SIZE - pn_length
        sample = protected_payload[sample_offset:sample_offset + 16]
        encryptor = self.hp.encryptor()
        buf = bytearray(31)
        encryptor.update_into(sample, buf)
        mask = buf[:5]

        packet = bytearray(plain_header + protected_payload)
        if is_long_header(packet[0]):
            # long header
            packet[0] ^= (mask[0] & 0x0f)
        else:
            # short header
            packet[0] ^= (mask[0] & 0x1f)

        for i in range(pn_length):
            packet[pn_offset + i] ^= mask[1 + i]

        return packet

    def is_valid(self):
        return self.aead is not None

    def setup(self, algorithm, secret):
        key, self.iv, hp = derive_key_iv_hp(algorithm, secret)
        self.aead = aead.AESGCM(key)
        self.aead_tag_size = 16
        self.hp = Cipher(algorithms.AES(hp), modes.ECB(), backend=default_backend())

    def setup_initial(self, cid, is_client):
        algorithm, secret = derive_initial_secret(cid, is_client)
        self.setup(algorithm, secret)

    def teardown(self):
        self.aead = None
        self.hp = None
        self.iv = None


class CryptoPair:
    def __init__(self):
        self.recv = CryptoContext()
        self.send = CryptoContext()

    @classmethod
    def initial(cls, cid, is_client):
        pair = cls()
        pair.setup_initial(cid, is_client)
        return pair

    def setup_initial(self, cid, is_client):
        self.recv.setup_initial(cid, not is_client)
        self.send.setup_initial(cid, is_client)
