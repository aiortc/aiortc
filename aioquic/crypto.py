from __future__ import annotations

import binascii
from typing import Optional, Tuple

from ._crypto import AEAD, CryptoError, HeaderProtection
from .packet import PACKET_NUMBER_MAX_SIZE, decode_packet_number, is_long_header
from .tls import CipherSuite, cipher_suite_hash, hkdf_expand_label, hkdf_extract

CIPHER_SUITES = {
    CipherSuite.AES_128_GCM_SHA256: (b"aes-128-ecb", b"aes-128-gcm"),
    CipherSuite.AES_256_GCM_SHA384: (b"aes-256-ecb", b"aes-256-gcm"),
    CipherSuite.CHACHA20_POLY1305_SHA256: (b"chacha20", b"chacha20-poly1305"),
}
INITIAL_CIPHER_SUITE = CipherSuite.AES_128_GCM_SHA256
INITIAL_SALT = binascii.unhexlify("ef4fb0abb47470c41befcf8031334fae485e09a0")
SAMPLE_SIZE = 16


def derive_key_iv_hp(
    cipher_suite: CipherSuite, secret: bytes
) -> Tuple[bytes, bytes, bytes]:
    algorithm = cipher_suite_hash(cipher_suite)
    if cipher_suite in [
        CipherSuite.AES_256_GCM_SHA384,
        CipherSuite.CHACHA20_POLY1305_SHA256,
    ]:
        key_size = 32
    else:
        key_size = 16
    return (
        hkdf_expand_label(algorithm, secret, b"quic key", b"", key_size),
        hkdf_expand_label(algorithm, secret, b"quic iv", b"", 12),
        hkdf_expand_label(algorithm, secret, b"quic hp", b"", key_size),
    )


class CryptoContext:
    def __init__(self, key_phase: int = 0) -> None:
        self.aead: Optional[AEAD]
        self.cipher_suite: Optional[CipherSuite]
        self.hp: Optional[HeaderProtection]
        self.iv: Optional[bytes]
        self.key_phase = key_phase
        self.secret: Optional[bytes]

        self.teardown()

    def apply_key_phase(self, crypto: CryptoContext) -> None:
        self.aead = crypto.aead
        self.iv = crypto.iv
        self.key_phase = crypto.key_phase
        self.secret = crypto.secret

    def decrypt_packet(
        self, packet: bytes, encrypted_offset: int, expected_packet_number: int
    ) -> Tuple[bytes, bytes, int, bool]:
        if self.aead is None:
            raise CryptoError("Decryption key is not available")

        # header protection
        sample_offset = encrypted_offset + PACKET_NUMBER_MAX_SIZE
        sample = packet[sample_offset : sample_offset + SAMPLE_SIZE]
        mask = self.hp.mask(sample)
        packet = bytearray(packet)

        if is_long_header(packet[0]):
            # long header
            packet[0] ^= mask[0] & 0x0F
        else:
            # short header
            packet[0] ^= mask[0] & 0x1F

        pn_length = (packet[0] & 0x03) + 1
        for i in range(pn_length):
            packet[encrypted_offset + i] ^= mask[1 + i]
        pn = packet[encrypted_offset : encrypted_offset + pn_length]
        plain_header = bytes(packet[: encrypted_offset + pn_length])

        # detect key phase change
        crypto = self
        if not is_long_header(packet[0]):
            key_phase = (packet[0] & 4) >> 2
            if key_phase != self.key_phase:
                crypto = self.next_key_phase()

        # payload protection
        nonce = crypto.iv[:-pn_length] + bytes(
            crypto.iv[i - pn_length] ^ pn[i] for i in range(pn_length)
        )
        payload = crypto.aead.decrypt(
            nonce, bytes(packet[encrypted_offset + pn_length :]), plain_header
        )

        # packet number
        packet_number = 0
        for i in range(pn_length):
            packet_number = (packet_number << 8) | pn[i]
        packet_number = decode_packet_number(
            packet_number, pn_length * 8, expected_packet_number
        )

        return plain_header, payload, packet_number, crypto != self

    def encrypt_packet(self, plain_header: bytes, plain_payload: bytes) -> bytes:
        assert self.is_valid(), "Encryption key is not available"

        pn_length = (plain_header[0] & 0x03) + 1
        pn_offset = len(plain_header) - pn_length
        pn = plain_header[pn_offset : pn_offset + pn_length]

        # payload protection
        nonce = self.iv[:-pn_length] + bytes(
            self.iv[i - pn_length] ^ pn[i] for i in range(pn_length)
        )
        protected_payload = self.aead.encrypt(nonce, plain_payload, plain_header)

        # header protection
        sample_offset = PACKET_NUMBER_MAX_SIZE - pn_length
        sample = protected_payload[sample_offset : sample_offset + SAMPLE_SIZE]
        mask = self.hp.mask(sample)

        packet = bytearray(plain_header + protected_payload)
        if is_long_header(packet[0]):
            # long header
            packet[0] ^= mask[0] & 0x0F
        else:
            # short header
            packet[0] ^= mask[0] & 0x1F

        for i in range(pn_length):
            packet[pn_offset + i] ^= mask[1 + i]

        return bytes(packet)

    def is_valid(self) -> bool:
        return self.aead is not None

    def next_key_phase(self) -> CryptoContext:
        algorithm = cipher_suite_hash(self.cipher_suite)

        crypto = CryptoContext(key_phase=int(not self.key_phase))
        crypto.setup(
            self.cipher_suite,
            hkdf_expand_label(
                algorithm, self.secret, b"traffic upd", b"", algorithm.digest_size
            ),
        )
        return crypto

    def setup(self, cipher_suite: CipherSuite, secret: bytes) -> None:
        hp_cipher_name, aead_cipher_name = CIPHER_SUITES[cipher_suite]

        key, self.iv, hp = derive_key_iv_hp(cipher_suite, secret)
        self.aead = AEAD(aead_cipher_name, key)
        self.cipher_suite = cipher_suite
        self.hp = HeaderProtection(hp_cipher_name, hp)
        self.secret = secret

    def teardown(self) -> None:
        self.aead = None
        self.cipher_suite = None
        self.hp = None
        self.iv = None
        self.secret = None


class CryptoPair:
    def __init__(self) -> None:
        self.aead_tag_size = 16
        self.recv = CryptoContext()
        self.send = CryptoContext()
        self._update_key_requested = False

    def decrypt_packet(
        self, packet: bytes, encrypted_offset: int, expected_packet_number: int
    ) -> Tuple[bytes, bytes, int]:
        plain_header, payload, packet_number, update_key = self.recv.decrypt_packet(
            packet, encrypted_offset, expected_packet_number
        )
        if update_key:
            self._update_key()
        return plain_header, payload, packet_number

    def encrypt_packet(self, plain_header: bytes, plain_payload: bytes) -> bytes:
        if self._update_key_requested:
            self._update_key()
        return self.send.encrypt_packet(plain_header, plain_payload)

    def setup_initial(self, cid: bytes, is_client: bool) -> None:
        if is_client:
            recv_label, send_label = b"server in", b"client in"
        else:
            recv_label, send_label = b"client in", b"server in"

        algorithm = cipher_suite_hash(INITIAL_CIPHER_SUITE)
        initial_secret = hkdf_extract(algorithm, INITIAL_SALT, cid)
        self.recv.setup(
            INITIAL_CIPHER_SUITE,
            hkdf_expand_label(
                algorithm, initial_secret, recv_label, b"", algorithm.digest_size
            ),
        )
        self.send.setup(
            INITIAL_CIPHER_SUITE,
            hkdf_expand_label(
                algorithm, initial_secret, send_label, b"", algorithm.digest_size
            ),
        )

    def teardown(self) -> None:
        self.recv.teardown()
        self.send.teardown()

    def update_key(self) -> None:
        self._update_key_requested = True

    @property
    def key_phase(self) -> int:
        if self._update_key_requested:
            return int(not self.recv.key_phase)
        else:
            return self.recv.key_phase

    def _update_key(self) -> None:
        self.recv.apply_key_phase(self.recv.next_key_phase())
        self.send.apply_key_phase(self.send.next_key_phase())
        self._update_key_requested = False
