import binascii
from unittest import TestCase

from aioquic import tls
from aioquic.tls import (Buffer, BufferReadError, ClientHello, Context,
                         ServerHello, pull_block, pull_bytes,
                         pull_client_hello, pull_server_hello, pull_uint8,
                         pull_uint16, pull_uint32, pull_uint64,
                         push_client_hello, push_server_hello)

from .utils import load


class BufferTest(TestCase):
    def test_pull_block_truncated(self):
        buf = Buffer(capacity=0)
        with self.assertRaises(BufferReadError):
            with pull_block(buf, 1):
                pass

    def test_pull_bytes_truncated(self):
        buf = Buffer(capacity=0)
        with self.assertRaises(BufferReadError):
            pull_bytes(buf, 2)

    def test_pull_uint8_truncated(self):
        buf = Buffer(capacity=0)
        with self.assertRaises(BufferReadError):
            pull_uint8(buf)

    def test_pull_uint16_truncated(self):
        buf = Buffer(capacity=1)
        with self.assertRaises(BufferReadError):
            pull_uint16(buf)

    def test_pull_uint32_truncated(self):
        buf = Buffer(capacity=3)
        with self.assertRaises(BufferReadError):
            pull_uint32(buf)

    def test_pull_uint64_truncated(self):
        buf = Buffer(capacity=7)
        with self.assertRaises(BufferReadError):
            pull_uint64(buf)

    def test_seek(self):
        buf = Buffer(data=b'01234567')
        self.assertFalse(buf.eof())
        self.assertEqual(buf.tell(), 0)

        buf.seek(4)
        self.assertFalse(buf.eof())
        self.assertEqual(buf.tell(), 4)

        buf.seek(8)
        self.assertTrue(buf.eof())
        self.assertEqual(buf.tell(), 8)


class ContextTest(TestCase):
    def test_client_hello(self):
        context = Context(is_client=True)
        hello = context.client_hello()

        self.assertEqual(len(hello.random), 32)
        self.assertEqual(len(hello.session_id), 32)


class TlsTest(TestCase):
    def test_pull_client_hello(self):
        buf = Buffer(data=load('client_hello.bin'))
        hello = pull_client_hello(buf)
        self.assertEqual(
            hello.random,
            binascii.unhexlify(
                '18b2b23bf3e44b5d52ccfe7aecbc5ff14eadc3d349fabf804d71f165ae76e7d5'))
        self.assertEqual(
            hello.session_id,
            binascii.unhexlify(
                '9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235'))
        self.assertEqual(hello.cipher_suites, [
            tls.CipherSuite.AES_256_GCM_SHA384,
            tls.CipherSuite.AES_128_GCM_SHA256,
            tls.CipherSuite.CHACHA20_POLY1305_SHA256,
        ])
        self.assertEqual(hello.compression_methods, [
            tls.CompressionMethod.NULL,
        ])

        # extensions
        self.assertEqual(hello.key_exchange_modes, [
            tls.KeyExchangeMode.PSK_DHE_KE,
        ])
        self.assertEqual(hello.key_share, [
            (
                tls.Group.SECP256R1,
                binascii.unhexlify(
                    '047bfea344467535054263b75def60cffa82405a211b68d1eb8d1d944e67aef8'
                    '93c7665a5473d032cfaf22a73da28eb4aacae0017ed12557b5791f98a1e84f15'
                    'b0'),
            )
        ])
        self.assertEqual(hello.signature_algorithms, [
            tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
            tls.SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
            tls.SignatureAlgorithm.RSA_PKCS1_SHA256,
            tls.SignatureAlgorithm.RSA_PKCS1_SHA1,
        ])
        self.assertEqual(hello.supported_groups, [
            tls.Group.SECP256R1,
        ])
        self.assertEqual(hello.supported_versions, [
            tls.TLS_VERSION_1_3,
            tls.TLS_VERSION_1_3_DRAFT_28,
            tls.TLS_VERSION_1_3_DRAFT_27,
            tls.TLS_VERSION_1_3_DRAFT_26,
        ])

    def test_push_client_hello(self):
        hello = ClientHello(
            random=binascii.unhexlify(
                '18b2b23bf3e44b5d52ccfe7aecbc5ff14eadc3d349fabf804d71f165ae76e7d5'),
            session_id=binascii.unhexlify(
                '9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235'),
            cipher_suites=[
                tls.CipherSuite.AES_256_GCM_SHA384,
                tls.CipherSuite.AES_128_GCM_SHA256,
                tls.CipherSuite.CHACHA20_POLY1305_SHA256,
            ],
            compression_methods=[
                tls.CompressionMethod.NULL,
            ],

            key_exchange_modes=[
                tls.KeyExchangeMode.PSK_DHE_KE,
            ],
            key_share=[
                (
                    tls.Group.SECP256R1,
                    binascii.unhexlify(
                        '047bfea344467535054263b75def60cffa82405a211b68d1eb8d1d944e67aef8'
                        '93c7665a5473d032cfaf22a73da28eb4aacae0017ed12557b5791f98a1e84f15'
                        'b0'),
                )
            ],
            signature_algorithms=[
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                tls.SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA1,
            ],
            supported_groups=[
                tls.Group.SECP256R1,
            ],
            supported_versions=[
                tls.TLS_VERSION_1_3,
                tls.TLS_VERSION_1_3_DRAFT_28,
                tls.TLS_VERSION_1_3_DRAFT_27,
                tls.TLS_VERSION_1_3_DRAFT_26,
            ])

        buf = Buffer(1000)
        push_client_hello(buf, hello)
        self.assertEqual(buf.data, load('client_hello.bin'))

    def test_pull_server_hello(self):
        buf = Buffer(data=load('server_hello.bin'))
        hello = pull_server_hello(buf)

        self.assertEqual(
            hello.random,
            binascii.unhexlify(
                'ada85271d19680c615ea7336519e3fdf6f1e26f3b1075ee1de96ffa8884e8280'))
        self.assertEqual(
            hello.session_id,
            binascii.unhexlify(
                '9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235'))
        self.assertEqual(hello.cipher_suite, tls.CipherSuite.AES_256_GCM_SHA384)
        self.assertEqual(hello.compression_method, tls.CompressionMethod.NULL)
        self.assertEqual(hello.key_share, (
            tls.Group.SECP256R1,
            binascii.unhexlify(
                '048b27d0282242d84b7fcc02a9c4f13eca0329e3c7029aa34a33794e6e7ba189'
                '5cca1c503bf0378ac6937c354912116ff3251026bca1958d7f387316c83ae6cf'
                'b2')
        ))
        self.assertEqual(hello.supported_version, tls.TLS_VERSION_1_3)

    def test_push_server_hello(self):
        hello = ServerHello(
            random=binascii.unhexlify(
                'ada85271d19680c615ea7336519e3fdf6f1e26f3b1075ee1de96ffa8884e8280'),
            session_id=binascii.unhexlify(
                '9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235'),
            cipher_suite=tls.CipherSuite.AES_256_GCM_SHA384,
            compression_method=tls.CompressionMethod.NULL,

            key_share=(
                tls.Group.SECP256R1,
                binascii.unhexlify(
                    '048b27d0282242d84b7fcc02a9c4f13eca0329e3c7029aa34a33794e6e7ba189'
                    '5cca1c503bf0378ac6937c354912116ff3251026bca1958d7f387316c83ae6cf'
                    'b2'),
            ),
            supported_version=tls.TLS_VERSION_1_3,
        )

        buf = Buffer(1000)
        push_server_hello(buf, hello)
        self.assertEqual(buf.data, load('server_hello.bin'))
