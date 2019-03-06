import logging
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
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

logger = logging.getLogger('tls')

TLS_VERSION_1_2 = 0x0303
TLS_VERSION_1_3 = 0x0304
TLS_VERSION_1_3_DRAFT_28 = 0x7f1c
TLS_VERSION_1_3_DRAFT_27 = 0x7f1b
TLS_VERSION_1_3_DRAFT_26 = 0x7f1a


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
    AES_256_GCM_SHA384 = 0x1302
    AES_128_GCM_SHA256 = 0x1301
    CHACHA20_POLY1305_SHA256 = 0x1303


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
    SECP256R1 = 23


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
    RSA_PSS_RSAE_SHA256 = 0x0804
    ECDSA_SECP256R1_SHA256 = 0x0403
    RSA_PKCS1_SHA256 = 0x0401
    RSA_PKCS1_SHA1 = 0x0201


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


# MESSAGES

@dataclass
class ClientHello:
    random: bytes = None
    session_id: bytes = None
    cipher_suites: List[int] = None
    compression_methods: List[int] = None

    # extensions
    key_exchange_modes: List[int] = None
    key_share: List[Tuple[int, bytes]] = None
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

            for extension_type, extension_value in hello.other_extensions:
                with push_extension(buf, extension_type):
                    push_bytes(buf, extension_value)

            with push_extension(buf, ExtensionType.PSK_KEY_EXCHANGE_MODES):
                push_list(buf, 1, push_uint8, hello.key_exchange_modes)


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
    def __init__(self):
        self.algorithm = hashes.SHA256()
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


class Context:
    def __init__(self, is_client):
        self.certificate = None
        self.certificate_private_key = None
        self.handshake_extensions = []
        self.update_traffic_key_cb = lambda d, e, s: None

        self._peer_certificate = None
        self._receive_buffer = b''
        self.enc_key = None
        self.dec_key = None

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

        self.is_client = is_client

    def handle_message(self, input_data, output_buf):
        if not input_data:
            self._client_send_hello(output_buf)
            return

        self._receive_buffer += input_data
        while len(self._receive_buffer) >= 4:
            # determine message length
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
            if self.state == State.CLIENT_EXPECT_SERVER_HELLO:
                self._client_handle_hello(input_buf, output_buf)
            elif self.state == State.CLIENT_EXPECT_ENCRYPTED_EXTENSIONS:
                self._client_handle_encrypted_extensions(input_buf, output_buf)
            elif self.state == State.CLIENT_EXPECT_CERTIFICATE_REQUEST_OR_CERTIFICATE:
                self._client_handle_certificate(input_buf, output_buf)
            elif self.state == State.CLIENT_EXPECT_CERTIFICATE_VERIFY:
                self._client_handle_certificate_verify(input_buf, output_buf)
            elif self.state == State.CLIENT_EXPECT_FINISHED:
                self._client_handle_finished(input_buf, output_buf)
            elif self.state == State.SERVER_EXPECT_CLIENT_HELLO:
                self._server_handle_hello(input_buf, output_buf)
            elif self.state == State.SERVER_EXPECT_FINISHED:
                self._server_handle_finished(input_buf, output_buf)
            else:
                raise Exception('unhandled state')

            assert input_buf.eof()

    def _client_send_hello(self, output_buf):
        hello = ClientHello(
            random=self.client_random,
            session_id=self.session_id,
            cipher_suites=[
                CipherSuite.AES_128_GCM_SHA256,
            ],
            compression_methods=[
                CompressionMethod.NULL,
            ],

            key_exchange_modes=[
                KeyExchangeMode.PSK_DHE_KE,
            ],
            key_share=[
                (
                    Group.SECP256R1,
                    self.private_key.public_key().public_bytes(
                        Encoding.X962, PublicFormat.UncompressedPoint),
                )
            ],
            signature_algorithms=[
                SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
                SignatureAlgorithm.RSA_PKCS1_SHA256,
                SignatureAlgorithm.RSA_PKCS1_SHA1,
            ],
            supported_groups=[
                Group.SECP256R1,
            ],
            supported_versions=[
                TLS_VERSION_1_3,
            ],

            other_extensions=self.handshake_extensions
        )

        self.key_schedule = KeySchedule()
        self.key_schedule.extract(None)

        with self._push_message(output_buf):
            push_client_hello(output_buf, hello)

        self._set_state(State.CLIENT_EXPECT_SERVER_HELLO)

    def _client_handle_hello(self, input_buf, output_buf):
        peer_hello = pull_server_hello(input_buf)

        peer_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), peer_hello.key_share[1])
        shared_key = self.private_key.exchange(ec.ECDH(), peer_public_key)

        self.key_schedule.update_hash(input_buf.data)
        self.key_schedule.extract(shared_key)

        self._setup_traffic_protection(Direction.DECRYPT, Epoch.HANDSHAKE, b's hs traffic')

        self._set_state(State.CLIENT_EXPECT_ENCRYPTED_EXTENSIONS)

    def _client_handle_encrypted_extensions(self, input_buf, output_buf):
        pull_encrypted_extensions(input_buf)

        self._setup_traffic_protection(Direction.ENCRYPT, Epoch.HANDSHAKE, b'c hs traffic')
        self.key_schedule.update_hash(input_buf.data)

        self._set_state(State.CLIENT_EXPECT_CERTIFICATE_REQUEST_OR_CERTIFICATE)

    def _client_handle_certificate(self, input_buf, output_buf):
        certificate = pull_certificate(input_buf)

        self._peer_certificate = x509.load_der_x509_certificate(
            certificate.certificates[0][0], backend=default_backend())
        self.key_schedule.update_hash(input_buf.data)

        self._set_state(State.CLIENT_EXPECT_CERTIFICATE_VERIFY)

    def _client_handle_certificate_verify(self, input_buf, output_buf):
        verify = pull_certificate_verify(input_buf)

        # check signature
        assert verify.algorithm == SignatureAlgorithm.RSA_PSS_RSAE_SHA256
        algorithm = hashes.SHA256()
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
        expected_verify_data = self.key_schedule.finished_verify_data(self.dec_key)
        assert finished.verify_data == expected_verify_data
        self.key_schedule.update_hash(input_buf.data)

        # prepare traffic keys
        assert self.key_schedule.generation == 2
        self.key_schedule.extract(None)
        self._setup_traffic_protection(Direction.DECRYPT, Epoch.ONE_RTT, b's ap traffic')
        next_enc_key = self.key_schedule.derive_secret(b'c ap traffic')

        # send finished
        push_finished(output_buf, Finished(
            verify_data=self.key_schedule.finished_verify_data(self.enc_key)))

        # commit traffic key
        self.enc_key = next_enc_key
        self.update_traffic_key_cb(Direction.ENCRYPT, Epoch.ONE_RTT, self.enc_key)

        self._set_state(State.CLIENT_POST_HANDSHAKE)

    def _server_handle_hello(self, input_buf, output_buf):
        peer_hello = pull_client_hello(input_buf)

        self.client_random = peer_hello.random
        self.server_random = os.urandom(32)
        self.session_id = peer_hello.session_id
        self.private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())

        self.key_schedule = KeySchedule()
        self.key_schedule.extract(None)
        self.key_schedule.update_hash(input_buf.data)

        peer_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), peer_hello.key_share[0][1])
        shared_key = self.private_key.exchange(ec.ECDH(), peer_public_key)

        # send hello
        hello = ServerHello(
            random=self.server_random,
            session_id=self.session_id,
            cipher_suite=CipherSuite.AES_128_GCM_SHA256,
            compression_method=CompressionMethod.NULL,

            key_share=(
                Group.SECP256R1,
                self.private_key.public_key().public_bytes(
                    Encoding.X962, PublicFormat.UncompressedPoint),
            ),
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
        algorithm = hashes.SHA256()
        signature = self.certificate_private_key.sign(
            self.key_schedule.certificate_verify_data(b'TLS 1.3, server CertificateVerify'),
            padding.PSS(
                mgf=padding.MGF1(algorithm),
                salt_length=algorithm.digest_size
            ),
            algorithm)
        with self._push_message(output_buf):
            push_certificate_verify(output_buf, CertificateVerify(
                algorithm=SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                signature=signature))

        # send finished
        with self._push_message(output_buf):
            push_finished(output_buf, Finished(
                verify_data=self.key_schedule.finished_verify_data(self.enc_key)))

        # prepare traffic keys
        assert self.key_schedule.generation == 2
        self.key_schedule.extract(None)
        self._setup_traffic_protection(Direction.ENCRYPT, Epoch.ONE_RTT, b's ap traffic')
        self._next_dec_key = self.key_schedule.derive_secret(b'c ap traffic')

        self._set_state(State.SERVER_EXPECT_FINISHED)

    def _server_handle_finished(self, input_buf, output_buf):
        finished = pull_finished(input_buf)

        # check verify data
        expected_verify_data = self.key_schedule.finished_verify_data(self.dec_key)
        assert finished.verify_data == expected_verify_data

        # commit traffic key
        self.dec_key = self._next_dec_key
        self._next_dec_key = None
        self.update_traffic_key_cb(Direction.DECRYPT, Epoch.ONE_RTT, self.dec_key)

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
            self.enc_key = key
        else:
            self.dec_key = key

        self.update_traffic_key_cb(direction, epoch, key)

    def _set_state(self, state):
        logger.info('%s -> %s', self.state, state)
        self.state = state
