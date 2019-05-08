import os
import struct
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from struct import pack_into, unpack_from
from typing import List, Tuple

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import ec, padding
from cryptography.hazmat.primitives.ciphers import aead
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

TLS_VERSION_1_2 = 0x0303
TLS_VERSION_1_3 = 0x0304
TLS_VERSION_1_3_DRAFT_28 = 0x7f1c
TLS_VERSION_1_3_DRAFT_27 = 0x7f1b
TLS_VERSION_1_3_DRAFT_26 = 0x7f1a


class AlertHandshakeFailure(Exception):
    pass


class AlertUnexpectedMessage(Exception):
    pass


class Direction(Enum):
    DECRYPT = 0
    ENCRYPT = 1


class Epoch(Enum):
    INITIAL = 0
    ZERO_RTT = 1
    HANDSHAKE = 2
    ONE_RTT = 3


class State(Enum):
    CLIENT_HANDSHAKE_START = 0
    CLIENT_EXPECT_SERVER_HELLO = 1
    CLIENT_EXPECT_ENCRYPTED_EXTENSIONS = 2
    CLIENT_EXPECT_CERTIFICATE_REQUEST_OR_CERTIFICATE = 3
    CLIENT_EXPECT_CERTIFICATE_CERTIFICATE = 4
    CLIENT_EXPECT_CERTIFICATE_VERIFY = 5
    CLIENT_EXPECT_FINISHED = 6
    CLIENT_POST_HANDSHAKE = 7

    SERVER_EXPECT_CLIENT_HELLO = 8
    SERVER_EXPECT_FINISHED = 9
    SERVER_POST_HANDSHAKE = 10


def hkdf_label(label, hash_value, length):
    full_label = b'tls13 ' + label
    return (
        struct.pack('!HB', length, len(full_label)) + full_label +
        struct.pack('!B', len(hash_value)) + hash_value)


def hkdf_expand_label(algorithm, secret, label, hash_value, length):
    return HKDFExpand(
        algorithm=algorithm,
        length=length,
        info=hkdf_label(label, hash_value, length),
        backend=default_backend()
    ).derive(secret)


def hkdf_extract(algorithm, salt, key_material):
    h = hmac.HMAC(salt, algorithm, backend=default_backend())
    h.update(key_material)
    return h.finalize()


class CipherSuite(IntEnum):
    AES_128_GCM_SHA256 = 0x1301
    AES_256_GCM_SHA384 = 0x1302
    CHACHA20_POLY1305_SHA256 = 0x1303
    EMPTY_RENEGOTIATION_INFO_SCSV = 0x00ff


class CompressionMethod(IntEnum):
    NULL = 0


class ExtensionType(IntEnum):
    SERVER_NAME = 0
    STATUS_REQUEST = 5
    SUPPORTED_GROUPS = 10
    SIGNATURE_ALGORITHMS = 13
    ALPN = 16
    COMPRESS_CERTIFICATE = 27
    PRE_SHARED_KEY = 41
    EARLY_DATA = 42
    SUPPORTED_VERSIONS = 43
    COOKIE = 44
    PSK_KEY_EXCHANGE_MODES = 45
    KEY_SHARE = 51
    QUIC_TRANSPORT_PARAMETERS = 65445
    ENCRYPTED_SERVER_NAME = 65486


class Group(IntEnum):
    SECP256R1 = 0x0017
    SECP384R1 = 0x0018
    SECP521R1 = 0x0019
    X25519 = 0x001d


class HandshakeType(IntEnum):
    CLIENT_HELLO = 1
    SERVER_HELLO = 2
    NEW_SESSION_TICKET = 4
    END_OF_EARLY_DATA = 5
    ENCRYPTED_EXTENSIONS = 8
    CERTIFICATE = 11
    CERTIFICATE_REQUEST = 13
    CERTIFICATE_VERIFY = 15
    FINISHED = 20
    KEY_UPDATE = 24
    COMPRESSED_CERTIFICATE = 25
    MESSAGE_HASH = 254


class KeyExchangeMode(IntEnum):
    PSK_DHE_KE = 1


class SignatureAlgorithm(IntEnum):
    ECDSA_SECP256R1_SHA256 = 0x0403
    ECDSA_SECP384R1_SHA384 = 0x0503
    ECDSA_SECP521R1_SHA512 = 0x0603
    ED25519 = 0x0807
    ED448 = 0x0808
    RSA_PKCS1_SHA1 = 0x0201
    RSA_PKCS1_SHA256 = 0x0401
    RSA_PKCS1_SHA384 = 0x0501
    RSA_PKCS1_SHA512 = 0x0601
    RSA_PSS_PSS_SHA256 = 0x0809
    RSA_PSS_PSS_SHA384 = 0x080a
    RSA_PSS_PSS_SHA512 = 0x080b
    RSA_PSS_RSAE_SHA256 = 0x0804
    RSA_PSS_RSAE_SHA384 = 0x0805
    RSA_PSS_RSAE_SHA512 = 0x0806


class BufferReadError(ValueError):
    pass


class Buffer:
    def __init__(self, capacity=None, data=None):
        if data is not None:
            self._data = data
            self._length = len(data)
        else:
            self._data = bytearray(capacity)
            self._length = capacity
        self._pos = 0

    @property
    def capacity(self):
        return self._length

    @property
    def data(self):
        return bytes(self._data[:self._pos])

    def data_slice(self, start, end):
        return bytes(self._data[start:end])

    def eof(self):
        return self._pos == self._length

    def seek(self, pos):
        assert pos <= self._length
        self._pos = pos

    def tell(self):
        return self._pos


# BYTES


def pull_bytes(buf: Buffer, length: int) -> bytes:
    """
    Pull bytes.
    """
    if buf._pos + length > buf._length:
        raise BufferReadError
    v = buf._data[buf._pos:buf._pos + length]
    buf._pos += length
    return v


def push_bytes(buf: Buffer, v: bytes):
    """
    Push bytes.
    """
    length = len(v)
    buf._data[buf._pos:buf._pos + length] = v
    buf._pos += length


# INTEGERS


def pull_uint8(buf: Buffer) -> int:
    """
    Pull an 8-bit unsigned integer.
    """
    try:
        v = buf._data[buf._pos]
        buf._pos += 1
        return v
    except IndexError:
        raise BufferReadError


def push_uint8(buf: Buffer, v: int):
    """
    Push an 8-bit unsigned integer.
    """
    buf._data[buf._pos] = v
    buf._pos += 1


def pull_uint16(buf: Buffer) -> int:
    """
    Pull a 16-bit unsigned integer.
    """
    try:
        v, = struct.unpack_from('!H', buf._data, buf._pos)
        buf._pos += 2
        return v
    except struct.error:
        raise BufferReadError


def push_uint16(buf: Buffer, v: int):
    """
    Push a 16-bit unsigned integer.
    """
    pack_into('!H', buf._data, buf._pos, v)
    buf._pos += 2


def pull_uint32(buf: Buffer) -> int:
    """
    Pull a 32-bit unsigned integer.
    """
    try:
        v, = struct.unpack_from('!L', buf._data, buf._pos)
        buf._pos += 4
        return v
    except struct.error:
        raise BufferReadError


def push_uint32(buf: Buffer, v: int):
    """
    Push a 32-bit unsigned integer.
    """
    pack_into('!L', buf._data, buf._pos, v)
    buf._pos += 4


def pull_uint64(buf: Buffer) -> int:
    """
    Pull a 64-bit unsigned integer.
    """
    try:
        v, = unpack_from('!Q', buf._data, buf._pos)
        buf._pos += 8
        return v
    except struct.error:
        raise BufferReadError


def push_uint64(buf: Buffer, v: int):
    """
    Push a 64-bit unsigned integer.
    """
    pack_into('!Q', buf._data, buf._pos, v)
    buf._pos += 8


# BLOCKS


@contextmanager
def pull_block(buf: Buffer, capacity: int):
    length = 0
    for b in pull_bytes(buf, capacity):
        length = (length << 8) | b
    end = buf._pos + length
    yield length
    assert buf._pos == end


@contextmanager
def push_block(buf: Buffer, capacity: int):
    """
    Context manager to push a variable-length block, with `capacity` bytes
    to write the length.
    """
    buf._pos += capacity
    start = buf._pos
    yield
    length = buf._pos - start
    while capacity:
        buf._data[start - capacity] = (length >> (8 * (capacity - 1))) & 0xff
        capacity -= 1


# LISTS


def pull_list(buf: Buffer, capacity: int, func):
    """
    Pull a list of items.
    """
    items = []
    with pull_block(buf, capacity) as length:
        end = buf._pos + length
        while buf._pos < end:
            items.append(func(buf))
    return items


def push_list(buf: Buffer, capacity: int, func, values):
    """
    Push a list of items.
    """
    with push_block(buf, capacity):
        for value in values:
            func(buf, value)


# KeyShareEntry


def pull_key_share(buf: Buffer) -> Tuple[int, bytes]:
    group = pull_uint16(buf)
    data_length = pull_uint16(buf)
    data = pull_bytes(buf, data_length)
    return (group, data)


def push_key_share(buf: Buffer, value: Tuple[int, bytes]):
    push_uint16(buf, value[0])
    with push_block(buf, 2):
        push_bytes(buf, value[1])


@contextmanager
def push_extension(buf: Buffer, extension_type: int):
    push_uint16(buf, extension_type)
    with push_block(buf, 2):
        yield


# ALPN


def pull_alpn_protocol(buf: Buffer) -> str:
    length = pull_uint8(buf)
    return pull_bytes(buf, length).decode('ascii')


def push_alpn_protocol(buf: Buffer, protocol: str):
    data = protocol.encode('ascii')
    push_uint8(buf, len(data))
    push_bytes(buf, data)


# MESSAGES

@dataclass
class ClientHello:
    random: bytes = None
    session_id: bytes = None
    cipher_suites: List[int] = None
    compression_methods: List[int] = None

    # extensions
    alpn_protocols: List[str] = None
    key_exchange_modes: List[int] = None
    key_share: List[Tuple[int, bytes]] = None
    server_name: str = None
    signature_algorithms: List[int] = None
    supported_groups: List[int] = None
    supported_versions: List[int] = None

    other_extensions: List[Tuple[int, bytes]] = field(default_factory=list)


def pull_client_hello(buf: Buffer):
    hello = ClientHello()

    assert pull_uint8(buf) == HandshakeType.CLIENT_HELLO
    with pull_block(buf, 3):
        assert pull_uint16(buf) == TLS_VERSION_1_2
        hello.random = pull_bytes(buf, 32)

        session_id_length = pull_uint8(buf)
        hello.session_id = pull_bytes(buf, session_id_length)

        hello.cipher_suites = pull_list(buf, 2, pull_uint16)
        hello.compression_methods = pull_list(buf, 1, pull_uint8)

        # extensions
        def pull_extension(buf):
            extension_type = pull_uint16(buf)
            extension_length = pull_uint16(buf)
            if extension_type == ExtensionType.KEY_SHARE:
                hello.key_share = pull_list(buf, 2, pull_key_share)
            elif extension_type == ExtensionType.SUPPORTED_VERSIONS:
                hello.supported_versions = pull_list(buf, 1, pull_uint16)
            elif extension_type == ExtensionType.SIGNATURE_ALGORITHMS:
                hello.signature_algorithms = pull_list(buf, 2, pull_uint16)
            elif extension_type == ExtensionType.SUPPORTED_GROUPS:
                hello.supported_groups = pull_list(buf, 2, pull_uint16)
            elif extension_type == ExtensionType.PSK_KEY_EXCHANGE_MODES:
                hello.key_exchange_modes = pull_list(buf, 1, pull_uint8)
            elif extension_type == ExtensionType.SERVER_NAME:
                with pull_block(buf, 2):
                    assert pull_uint8(buf) == 0
                    with pull_block(buf, 2) as length:
                        hello.server_name = pull_bytes(buf, length).decode('ascii')
            elif extension_type == ExtensionType.ALPN:
                hello.alpn_protocols = pull_list(buf, 2, pull_alpn_protocol)
            else:
                hello.other_extensions.append(
                    (extension_type, pull_bytes(buf, extension_length)),
                )

        pull_list(buf, 2, pull_extension)

    return hello


def push_client_hello(buf: Buffer, hello: ClientHello):
    push_uint8(buf, HandshakeType.CLIENT_HELLO)
    with push_block(buf, 3):
        push_uint16(buf, TLS_VERSION_1_2)
        push_bytes(buf, hello.random)
        with push_block(buf, 1):
            push_bytes(buf, hello.session_id)
        push_list(buf, 2, push_uint16, hello.cipher_suites)
        push_list(buf, 1, push_uint8, hello.compression_methods)

        # extensions
        with push_block(buf, 2):
            with push_extension(buf, ExtensionType.KEY_SHARE):
                push_list(buf, 2, push_key_share, hello.key_share)

            with push_extension(buf, ExtensionType.SUPPORTED_VERSIONS):
                push_list(buf, 1, push_uint16, hello.supported_versions)

            with push_extension(buf, ExtensionType.SIGNATURE_ALGORITHMS):
                push_list(buf, 2, push_uint16, hello.signature_algorithms)

            with push_extension(buf, ExtensionType.SUPPORTED_GROUPS):
                push_list(buf, 2, push_uint16, hello.supported_groups)

            with push_extension(buf, ExtensionType.PSK_KEY_EXCHANGE_MODES):
                push_list(buf, 1, push_uint8, hello.key_exchange_modes)

            if hello.server_name is not None:
                with push_extension(buf, ExtensionType.SERVER_NAME):
                    with push_block(buf, 2):
                        push_uint8(buf, 0)
                        with push_block(buf, 2):
                            push_bytes(buf, hello.server_name.encode('ascii'))

            if hello.alpn_protocols is not None:
                with push_extension(buf, ExtensionType.ALPN):
                    push_list(buf, 2, push_alpn_protocol, hello.alpn_protocols)

            for extension_type, extension_value in hello.other_extensions:
                with push_extension(buf, extension_type):
                    push_bytes(buf, extension_value)


@dataclass
class ServerHello:
    random: bytes = None
    session_id: bytes = None
    cipher_suite: int = None
    compression_method: int = None

    # extensions
    key_share: Tuple[int, bytes] = None
    supported_version: int = None


def pull_server_hello(buf: Buffer) -> ServerHello:
    hello = ServerHello()

    assert pull_uint8(buf) == HandshakeType.SERVER_HELLO
    with pull_block(buf, 3):
        assert pull_uint16(buf) == TLS_VERSION_1_2
        hello.random = pull_bytes(buf, 32)
        session_id_length = pull_uint8(buf)
        hello.session_id = pull_bytes(buf, session_id_length)
        hello.cipher_suite = pull_uint16(buf)
        hello.compression_method = pull_uint8(buf)

        # extensions
        def pull_extension(buf):
            extension_type = pull_uint16(buf)
            extension_length = pull_uint16(buf)
            if extension_type == ExtensionType.SUPPORTED_VERSIONS:
                hello.supported_version = pull_uint16(buf)
            elif extension_type == ExtensionType.KEY_SHARE:
                hello.key_share = pull_key_share(buf)
            else:
                pull_bytes(buf, extension_length)

        pull_list(buf, 2, pull_extension)

    return hello


def push_server_hello(buf: Buffer, hello: ServerHello):
    push_uint8(buf, HandshakeType.SERVER_HELLO)
    with push_block(buf, 3):
        push_uint16(buf, TLS_VERSION_1_2)
        push_bytes(buf, hello.random)

        with push_block(buf, 1):
            push_bytes(buf, hello.session_id)

        push_uint16(buf, hello.cipher_suite)
        push_uint8(buf, hello.compression_method)

        # extensions
        with push_block(buf, 2):
            with push_extension(buf, ExtensionType.SUPPORTED_VERSIONS):
                push_uint16(buf, hello.supported_version)

            with push_extension(buf, ExtensionType.KEY_SHARE):
                push_key_share(buf, hello.key_share)


@dataclass
class NewSessionTicket:
    lifetime_hint: int = 0
    ticket: bytes = b''


def pull_new_session_ticket(buf: Buffer) -> NewSessionTicket:
    new_session_ticket = NewSessionTicket()

    assert pull_uint8(buf) == HandshakeType.NEW_SESSION_TICKET
    with pull_block(buf, 3) as length:
        new_session_ticket.lifetime_hint = pull_uint32(buf)
        new_session_ticket.ticket = pull_bytes(buf, length - 4)

    return new_session_ticket


@dataclass
class EncryptedExtensions:
    other_extensions: List[Tuple[int, bytes]] = field(default_factory=list)


def pull_encrypted_extensions(buf: Buffer) -> EncryptedExtensions:
    extensions = EncryptedExtensions()

    assert pull_uint8(buf) == HandshakeType.ENCRYPTED_EXTENSIONS
    with pull_block(buf, 3):
        def pull_extension(buf):
            extension_type = pull_uint16(buf)
            extension_length = pull_uint16(buf)
            extensions.other_extensions.append(
                (extension_type, pull_bytes(buf, extension_length)),
            )

        pull_list(buf, 2, pull_extension)

    return extensions


def push_encrypted_extensions(buf: Buffer, extensions: EncryptedExtensions):
    push_uint8(buf, HandshakeType.ENCRYPTED_EXTENSIONS)
    with push_block(buf, 3):
        with push_block(buf, 2):
            for extension_type, extension_value in extensions.other_extensions:
                with push_extension(buf, extension_type):
                    push_bytes(buf, extension_value)


@dataclass
class Certificate:
    request_context: bytes = b''
    certificates: List = field(default_factory=list)


def pull_certificate(buf: Buffer) -> Certificate:
    certificate = Certificate()

    assert pull_uint8(buf) == HandshakeType.CERTIFICATE
    with pull_block(buf, 3):
        with pull_block(buf, 1) as length:
            certificate.request_context = pull_bytes(buf, length)

        def pull_certificate_entry(buf):
            with pull_block(buf, 3) as length:
                data = pull_bytes(buf, length)
            with pull_block(buf, 2) as length:
                extensions = pull_bytes(buf, length)
            return (data, extensions)

        certificate.certificates = pull_list(buf, 3, pull_certificate_entry)

    return certificate


def push_certificate(buf: Buffer, certificate: Certificate):
    push_uint8(buf, HandshakeType.CERTIFICATE)
    with push_block(buf, 3):
        with push_block(buf, 1):
            push_bytes(buf, certificate.request_context)

        def push_certificate_entry(buf, entry):
            with push_block(buf, 3):
                push_bytes(buf, entry[0])
            with push_block(buf, 2):
                push_bytes(buf, entry[1])

        push_list(buf, 3, push_certificate_entry, certificate.certificates)


@dataclass
class CertificateVerify:
    algorithm: int = None
    signature: bytes = None


def pull_certificate_verify(buf: Buffer) -> CertificateVerify:
    verify = CertificateVerify()

    assert pull_uint8(buf) == HandshakeType.CERTIFICATE_VERIFY
    with pull_block(buf, 3):
        verify.algorithm = pull_uint16(buf)
        with pull_block(buf, 2) as length:
            verify.signature = pull_bytes(buf, length)

    return verify


def push_certificate_verify(buf: Buffer, verify: CertificateVerify):
    push_uint8(buf, HandshakeType.CERTIFICATE_VERIFY)
    with push_block(buf, 3):
        push_uint16(buf, verify.algorithm)
        with push_block(buf, 2):
            push_bytes(buf, verify.signature)


@dataclass
class Finished:
    verify_data: bytes = b''


def pull_finished(buf: Buffer) -> Finished:
    finished = Finished()

    assert pull_uint8(buf) == HandshakeType.FINISHED
    with pull_block(buf, 3) as length:
        finished.verify_data = pull_bytes(buf, length)

    return finished


def push_finished(buf: Buffer, finished: Finished):
    push_uint8(buf, HandshakeType.FINISHED)
    with push_block(buf, 3):
        push_bytes(buf, finished.verify_data)


# CONTEXT


class KeySchedule:
    def __init__(self, cipher_suite):
        self.algorithm = cipher_suite_hash(cipher_suite)
        self.cipher_suite = cipher_suite
        self.generation = 0
        self.hash = hashes.Hash(self.algorithm, default_backend())
        self.hash_empty_value = self.hash.copy().finalize()
        self.secret = bytes(self.algorithm.digest_size)

    def certificate_verify_data(self, context_string):
        return b' ' * 64 + context_string + b'\x00' + self.hash.copy().finalize()

    def finished_verify_data(self, secret):
        hmac_key = hkdf_expand_label(
            algorithm=self.algorithm,
            secret=secret,
            label=b'finished',
            hash_value=b'',
            length=self.algorithm.digest_size)

        h = hmac.HMAC(hmac_key, algorithm=self.algorithm, backend=default_backend())
        h.update(self.hash.copy().finalize())
        return h.finalize()

    def derive_secret(self, label):
        return hkdf_expand_label(
            algorithm=self.algorithm,
            secret=self.secret,
            label=label,
            hash_value=self.hash.copy().finalize(),
            length=self.algorithm.digest_size)

    def extract(self, key_material=None):
        if key_material is None:
            key_material = bytes(self.algorithm.digest_size)

        if self.generation:
            self.secret = hkdf_expand_label(
                algorithm=self.algorithm,
                secret=self.secret,
                label=b'derived',
                hash_value=self.hash_empty_value,
                length=self.algorithm.digest_size)

        self.generation += 1
        self.secret = hkdf_extract(
            algorithm=self.algorithm,
            salt=self.secret,
            key_material=key_material)

    def update_hash(self, data):
        self.hash.update(data)


class KeyScheduleProxy:
    def __init__(self, cipher_suites):
        self.__items = list(map(KeySchedule, cipher_suites))

    def extract(self, key_material=None):
        for k in self.__items:
            k.extract(key_material)

    def select(self, cipher_suite):
        for k in self.__items:
            if k.cipher_suite == cipher_suite:
                return k

    def update_hash(self, data):
        for k in self.__items:
            k.update_hash(data)


CIPHER_SUITES = {
    CipherSuite.AES_128_GCM_SHA256: (aead.AESGCM, hashes.SHA256),
    CipherSuite.AES_256_GCM_SHA384: (aead.AESGCM, hashes.SHA384),
    CipherSuite.CHACHA20_POLY1305_SHA256: (aead.ChaCha20Poly1305, hashes.SHA256),
}

SIGNATURE_ALGORITHMS = {
    SignatureAlgorithm.RSA_PSS_RSAE_SHA256: hashes.SHA256,
    SignatureAlgorithm.RSA_PSS_RSAE_SHA384: hashes.SHA384,
    SignatureAlgorithm.RSA_PSS_RSAE_SHA512: hashes.SHA512,
}

GROUP_TO_CURVE = {
    Group.SECP256R1: ec.SECP256R1,
    Group.SECP384R1: ec.SECP384R1,
    Group.SECP521R1: ec.SECP521R1,
}
CURVE_TO_GROUP = dict((v, k) for k, v in GROUP_TO_CURVE.items())


def cipher_suite_aead(cipher_suite, key):
    return CIPHER_SUITES[cipher_suite][0](key)


def cipher_suite_hash(cipher_suite):
    return CIPHER_SUITES[cipher_suite][1]()


def decode_public_key(key_share):
    return ec.EllipticCurvePublicKey.from_encoded_point(
        GROUP_TO_CURVE[key_share[0]](), key_share[1])


def encode_public_key(public_key):
    return (
        CURVE_TO_GROUP[public_key.curve.__class__],
        public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint),
    )


def negotiate(supported, offered):
    for c in supported:
        if c in offered:
            return c


class Context:
    def __init__(self, is_client, logger=None):
        self.alpn_protocols = None
        self.certificate = None
        self.certificate_private_key = None
        self.handshake_extensions = []
        self.is_client = is_client
        self.server_name = None
        self.update_traffic_key_cb = lambda d, e, s: None

        self._cipher_suites = [
            CipherSuite.AES_256_GCM_SHA384,
            CipherSuite.AES_128_GCM_SHA256,
            CipherSuite.CHACHA20_POLY1305_SHA256,
        ]
        self._signature_algorithms = [
            SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
        ]

        self._peer_certificate = None
        self._receive_buffer = b''
        self._enc_key = None
        self._dec_key = None
        self.__logger = logger

        if is_client:
            self.client_random = os.urandom(32)
            self.session_id = os.urandom(32)
            self.private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
            self.state = State.CLIENT_HANDSHAKE_START
        else:
            self.client_random = None
            self.session_id = None
            self.private_key = None
            self.state = State.SERVER_EXPECT_CLIENT_HELLO

    def handle_message(self, input_data, output_buf):
        if self.state == State.CLIENT_HANDSHAKE_START:
            self._client_send_hello(output_buf[Epoch.INITIAL])
            return

        self._receive_buffer += input_data
        while len(self._receive_buffer) >= 4:
            # determine message length
            message_type = self._receive_buffer[0]
            message_length = 0
            for b in self._receive_buffer[1:4]:
                message_length = (message_length << 8) | b
            message_length += 4

            # check message is complete
            if len(self._receive_buffer) < message_length:
                break
            message = self._receive_buffer[:message_length]
            self._receive_buffer = self._receive_buffer[message_length:]

            input_buf = Buffer(data=message)

            # client states

            if self.state == State.CLIENT_EXPECT_SERVER_HELLO:
                if message_type == HandshakeType.SERVER_HELLO:
                    self._client_handle_hello(input_buf, output_buf[Epoch.INITIAL])
                else:
                    raise AlertUnexpectedMessage
            elif self.state == State.CLIENT_EXPECT_ENCRYPTED_EXTENSIONS:
                if message_type == HandshakeType.ENCRYPTED_EXTENSIONS:
                    self._client_handle_encrypted_extensions(input_buf)
                else:
                    raise AlertUnexpectedMessage
            elif self.state == State.CLIENT_EXPECT_CERTIFICATE_REQUEST_OR_CERTIFICATE:
                if message_type == HandshakeType.CERTIFICATE:
                    self._client_handle_certificate(input_buf)
                else:
                    # FIXME: handle certificate request
                    raise AlertUnexpectedMessage
            elif self.state == State.CLIENT_EXPECT_CERTIFICATE_VERIFY:
                if message_type == HandshakeType.CERTIFICATE_VERIFY:
                    self._client_handle_certificate_verify(input_buf)
                else:
                    raise AlertUnexpectedMessage
            elif self.state == State.CLIENT_EXPECT_FINISHED:
                if message_type == HandshakeType.FINISHED:
                    self._client_handle_finished(input_buf, output_buf[Epoch.HANDSHAKE])
                else:
                    raise AlertUnexpectedMessage
            elif self.state == State.CLIENT_POST_HANDSHAKE:
                if message_type == HandshakeType.NEW_SESSION_TICKET:
                    self._client_handle_new_session_ticket(input_buf)
                else:
                    raise AlertUnexpectedMessage

            # server states

            elif self.state == State.SERVER_EXPECT_CLIENT_HELLO:
                if message_type == HandshakeType.CLIENT_HELLO:
                    self._server_handle_hello(input_buf, output_buf[Epoch.INITIAL])
                else:
                    raise AlertUnexpectedMessage
            elif self.state == State.SERVER_EXPECT_FINISHED:
                if message_type == HandshakeType.FINISHED:
                    self._server_handle_finished(input_buf)
                else:
                    raise AlertUnexpectedMessage
            elif self.state == State.SERVER_POST_HANDSHAKE:
                raise AlertUnexpectedMessage

            # should not happen

            else:
                raise Exception('unhandled state')

            assert input_buf.eof()

    def _client_send_hello(self, output_buf):
        hello = ClientHello(
            random=self.client_random,
            session_id=self.session_id,
            cipher_suites=self._cipher_suites,
            compression_methods=[
                CompressionMethod.NULL,
            ],

            alpn_protocols=self.alpn_protocols,
            key_exchange_modes=[
                KeyExchangeMode.PSK_DHE_KE,
            ],
            key_share=[
                encode_public_key(self.private_key.public_key()),
            ],
            server_name=self.server_name,
            signature_algorithms=self._signature_algorithms,
            supported_groups=[
                Group.SECP256R1,
            ],
            supported_versions=[
                TLS_VERSION_1_3,
            ],

            other_extensions=self.handshake_extensions
        )

        self.key_schedule = KeyScheduleProxy(hello.cipher_suites)
        self.key_schedule.extract(None)

        with self._push_message(output_buf):
            push_client_hello(output_buf, hello)

        self._set_state(State.CLIENT_EXPECT_SERVER_HELLO)

    def _client_handle_hello(self, input_buf, output_buf):
        peer_hello = pull_server_hello(input_buf)

        peer_public_key = decode_public_key(peer_hello.key_share)
        shared_key = self.private_key.exchange(ec.ECDH(), peer_public_key)

        self.key_schedule = self.key_schedule.select(peer_hello.cipher_suite)
        self.key_schedule.update_hash(input_buf.data)
        self.key_schedule.extract(shared_key)

        self._setup_traffic_protection(Direction.DECRYPT, Epoch.HANDSHAKE, b's hs traffic')

        self._set_state(State.CLIENT_EXPECT_ENCRYPTED_EXTENSIONS)

    def _client_handle_encrypted_extensions(self, input_buf):
        pull_encrypted_extensions(input_buf)

        self._setup_traffic_protection(Direction.ENCRYPT, Epoch.HANDSHAKE, b'c hs traffic')
        self.key_schedule.update_hash(input_buf.data)

        self._set_state(State.CLIENT_EXPECT_CERTIFICATE_REQUEST_OR_CERTIFICATE)

    def _client_handle_certificate(self, input_buf):
        certificate = pull_certificate(input_buf)

        self._peer_certificate = x509.load_der_x509_certificate(
            certificate.certificates[0][0], backend=default_backend())
        self.key_schedule.update_hash(input_buf.data)

        self._set_state(State.CLIENT_EXPECT_CERTIFICATE_VERIFY)

    def _client_handle_certificate_verify(self, input_buf):
        verify = pull_certificate_verify(input_buf)

        # check signature
        algorithm = SIGNATURE_ALGORITHMS[verify.algorithm]()
        self._peer_certificate.public_key().verify(
            verify.signature,
            self.key_schedule.certificate_verify_data(b'TLS 1.3, server CertificateVerify'),
            padding.PSS(
                mgf=padding.MGF1(algorithm),
                salt_length=algorithm.digest_size
            ),
            algorithm)

        self.key_schedule.update_hash(input_buf.data)

        self._set_state(State.CLIENT_EXPECT_FINISHED)

    def _client_handle_finished(self, input_buf, output_buf):
        finished = pull_finished(input_buf)

        # check verify data
        expected_verify_data = self.key_schedule.finished_verify_data(self._dec_key)
        assert finished.verify_data == expected_verify_data
        self.key_schedule.update_hash(input_buf.data)

        # prepare traffic keys
        assert self.key_schedule.generation == 2
        self.key_schedule.extract(None)
        self._setup_traffic_protection(Direction.DECRYPT, Epoch.ONE_RTT, b's ap traffic')
        next_enc_key = self.key_schedule.derive_secret(b'c ap traffic')

        # send finished
        push_finished(output_buf, Finished(
            verify_data=self.key_schedule.finished_verify_data(self._enc_key)))

        # commit traffic key
        self._enc_key = next_enc_key
        self.update_traffic_key_cb(Direction.ENCRYPT, Epoch.ONE_RTT, self._enc_key)

        self._set_state(State.CLIENT_POST_HANDSHAKE)

    def _client_handle_new_session_ticket(self, input_buf):
        pull_new_session_ticket(input_buf)

    def _server_handle_hello(self, input_buf, output_buf):
        peer_hello = pull_client_hello(input_buf)

        # negotiate cipher suite
        cipher_suite = negotiate(self._cipher_suites, peer_hello.cipher_suites)
        if cipher_suite is None:
            raise AlertHandshakeFailure('No supported cipher suites')

        # negotiate signature algorithm
        signature_algorithm = negotiate(self._signature_algorithms, peer_hello.signature_algorithms)
        if signature_algorithm is None:
            raise AlertHandshakeFailure('No supported signature algorithms')

        self.client_random = peer_hello.random
        self.server_random = os.urandom(32)
        self.session_id = peer_hello.session_id
        self.private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())

        self.key_schedule = KeySchedule(cipher_suite)
        self.key_schedule.extract(None)
        self.key_schedule.update_hash(input_buf.data)

        peer_public_key = decode_public_key(peer_hello.key_share[0])
        shared_key = self.private_key.exchange(ec.ECDH(), peer_public_key)

        # send hello
        hello = ServerHello(
            random=self.server_random,
            session_id=self.session_id,
            cipher_suite=cipher_suite,
            compression_method=CompressionMethod.NULL,

            key_share=encode_public_key(self.private_key.public_key()),
            supported_version=TLS_VERSION_1_3,
        )
        with self._push_message(output_buf):
            push_server_hello(output_buf, hello)
        self.key_schedule.extract(shared_key)

        self._setup_traffic_protection(Direction.ENCRYPT, Epoch.HANDSHAKE, b's hs traffic')
        self._setup_traffic_protection(Direction.DECRYPT, Epoch.HANDSHAKE, b'c hs traffic')

        # send encrypted extensions
        with self._push_message(output_buf):
            push_encrypted_extensions(output_buf, EncryptedExtensions(
                other_extensions=self.handshake_extensions))

        # send certificate
        with self._push_message(output_buf):
            push_certificate(output_buf, Certificate(
                request_context=b'',
                certificates=[
                    (self.certificate.public_bytes(Encoding.DER), b'')
                ]))

        # send certificate verify
        algorithm = SIGNATURE_ALGORITHMS[signature_algorithm]()
        signature = self.certificate_private_key.sign(
            self.key_schedule.certificate_verify_data(b'TLS 1.3, server CertificateVerify'),
            padding.PSS(
                mgf=padding.MGF1(algorithm),
                salt_length=algorithm.digest_size
            ),
            algorithm)
        with self._push_message(output_buf):
            push_certificate_verify(output_buf, CertificateVerify(
                algorithm=signature_algorithm,
                signature=signature))

        # send finished
        with self._push_message(output_buf):
            push_finished(output_buf, Finished(
                verify_data=self.key_schedule.finished_verify_data(self._enc_key)))

        # prepare traffic keys
        assert self.key_schedule.generation == 2
        self.key_schedule.extract(None)
        self._setup_traffic_protection(Direction.ENCRYPT, Epoch.ONE_RTT, b's ap traffic')
        self._next_dec_key = self.key_schedule.derive_secret(b'c ap traffic')

        self._set_state(State.SERVER_EXPECT_FINISHED)

    def _server_handle_finished(self, input_buf):
        finished = pull_finished(input_buf)

        # check verify data
        expected_verify_data = self.key_schedule.finished_verify_data(self._dec_key)
        assert finished.verify_data == expected_verify_data

        # commit traffic key
        self._dec_key = self._next_dec_key
        self._next_dec_key = None
        self.update_traffic_key_cb(Direction.DECRYPT, Epoch.ONE_RTT, self._dec_key)

        self.key_schedule.update_hash(input_buf.data)

        self._set_state(State.SERVER_POST_HANDSHAKE)

    @contextmanager
    def _push_message(self, buf: Buffer):
        hash_start = buf.tell()
        yield
        self.key_schedule.update_hash(buf.data_slice(hash_start, buf.tell()))

    def _setup_traffic_protection(self, direction, epoch, label):
        key = self.key_schedule.derive_secret(label)

        if direction == Direction.ENCRYPT:
            self._enc_key = key
        else:
            self._dec_key = key

        self.update_traffic_key_cb(direction, epoch, key)

    def _set_state(self, state):
        if self.__logger:
            self.__logger.info('TLS %s -> %s', self.state, state)
        self.state = state
