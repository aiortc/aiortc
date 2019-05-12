import binascii
from unittest import TestCase

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization

from aioquic import tls
from aioquic.tls import (
    Buffer,
    BufferReadError,
    Certificate,
    CertificateVerify,
    ClientHello,
    Context,
    EncryptedExtensions,
    Finished,
    ServerHello,
    State,
    pull_block,
    pull_bytes,
    pull_certificate,
    pull_certificate_verify,
    pull_client_hello,
    pull_encrypted_extensions,
    pull_finished,
    pull_new_session_ticket,
    pull_server_hello,
    pull_uint8,
    pull_uint16,
    pull_uint32,
    pull_uint64,
    push_certificate,
    push_certificate_verify,
    push_client_hello,
    push_encrypted_extensions,
    push_finished,
    push_server_hello,
)

from .utils import load

SERVER_CERTIFICATE = x509.load_pem_x509_certificate(
    load("ssl_cert.pem"), backend=default_backend()
)
SERVER_PRIVATE_KEY = serialization.load_pem_private_key(
    load("ssl_key.pem"), password=None, backend=default_backend()
)

CERTIFICATE_DATA = load("tls_certificate.bin")[11:-2]
CERTIFICATE_VERIFY_SIGNATURE = load("tls_certificate_verify.bin")[-384:]

CLIENT_QUIC_TRANSPORT_PARAMETERS = binascii.unhexlify(
    b"ff0000110031000500048010000000060004801000000007000480100000000"
    b"4000481000000000100024258000800024064000a00010a"
)

SERVER_QUIC_TRANSPORT_PARAMETERS = binascii.unhexlify(
    b"ff00001104ff000011004500050004801000000006000480100000000700048"
    b"010000000040004810000000001000242580002001000000000000000000000"
    b"000000000000000800024064000a00010a"
)


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
        buf = Buffer(data=b"01234567")
        self.assertFalse(buf.eof())
        self.assertEqual(buf.tell(), 0)

        buf.seek(4)
        self.assertFalse(buf.eof())
        self.assertEqual(buf.tell(), 4)

        buf.seek(8)
        self.assertTrue(buf.eof())
        self.assertEqual(buf.tell(), 8)


def create_buffers():
    return {
        tls.Epoch.INITIAL: Buffer(capacity=4096),
        tls.Epoch.HANDSHAKE: Buffer(capacity=4096),
        tls.Epoch.ONE_RTT: Buffer(capacity=4096),
    }


def merge_buffers(buffers):
    return b"".join(x.data for x in buffers.values())


def reset_buffers(buffers):
    for k in buffers.keys():
        buffers[k].seek(0)


class ContextTest(TestCase):
    def create_client(self):
        client = Context(is_client=True)
        client.handshake_extensions = [
            (
                tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                CLIENT_QUIC_TRANSPORT_PARAMETERS,
            )
        ]
        self.assertEqual(client.state, State.CLIENT_HANDSHAKE_START)
        return client

    def create_server(self):
        server = Context(is_client=False)
        server.certificate = SERVER_CERTIFICATE
        server.certificate_private_key = SERVER_PRIVATE_KEY
        server.handshake_extensions = [
            (
                tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                SERVER_QUIC_TRANSPORT_PARAMETERS,
            )
        ]
        self.assertEqual(server.state, State.SERVER_EXPECT_CLIENT_HELLO)
        return server

    def test_client_unexpected_message(self):
        client = self.create_client()

        client.state = State.CLIENT_EXPECT_SERVER_HELLO
        with self.assertRaises(tls.AlertUnexpectedMessage):
            client.handle_message(b"\x00\x00\x00\x00", create_buffers())

        client.state = State.CLIENT_EXPECT_ENCRYPTED_EXTENSIONS
        with self.assertRaises(tls.AlertUnexpectedMessage):
            client.handle_message(b"\x00\x00\x00\x00", create_buffers())

        client.state = State.CLIENT_EXPECT_CERTIFICATE_REQUEST_OR_CERTIFICATE
        with self.assertRaises(tls.AlertUnexpectedMessage):
            client.handle_message(b"\x00\x00\x00\x00", create_buffers())

        client.state = State.CLIENT_EXPECT_CERTIFICATE_VERIFY
        with self.assertRaises(tls.AlertUnexpectedMessage):
            client.handle_message(b"\x00\x00\x00\x00", create_buffers())

        client.state = State.CLIENT_EXPECT_FINISHED
        with self.assertRaises(tls.AlertUnexpectedMessage):
            client.handle_message(b"\x00\x00\x00\x00", create_buffers())

        client.state = State.CLIENT_POST_HANDSHAKE
        with self.assertRaises(tls.AlertUnexpectedMessage):
            client.handle_message(b"\x00\x00\x00\x00", create_buffers())

    def test_server_unexpected_message(self):
        server = self.create_server()

        server.state = State.SERVER_EXPECT_CLIENT_HELLO
        with self.assertRaises(tls.AlertUnexpectedMessage):
            server.handle_message(b"\x00\x00\x00\x00", create_buffers())

        server.state = State.SERVER_EXPECT_FINISHED
        with self.assertRaises(tls.AlertUnexpectedMessage):
            server.handle_message(b"\x00\x00\x00\x00", create_buffers())

        server.state = State.SERVER_POST_HANDSHAKE
        with self.assertRaises(tls.AlertUnexpectedMessage):
            server.handle_message(b"\x00\x00\x00\x00", create_buffers())

    def _server_fail_hello(self, client, server):
        # send client hello
        client_buf = create_buffers()
        client.handle_message(b"", client_buf)
        self.assertEqual(client.state, State.CLIENT_EXPECT_SERVER_HELLO)
        server_input = merge_buffers(client_buf)
        reset_buffers(client_buf)

        # handle client hello
        server_buf = create_buffers()
        server.handle_message(server_input, server_buf)

    def test_server_unsupported_cipher_suite(self):
        client = self.create_client()
        client._cipher_suites = [tls.CipherSuite.AES_128_GCM_SHA256]

        server = self.create_server()
        server._cipher_suites = [tls.CipherSuite.AES_256_GCM_SHA384]

        with self.assertRaises(tls.AlertHandshakeFailure) as cm:
            self._server_fail_hello(client, server)
        self.assertEqual(str(cm.exception), "No supported cipher suite")

    def test_server_unsupported_signature_algorithm(self):
        client = self.create_client()
        client._signature_algorithms = [tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256]

        server = self.create_server()
        server._signature_algorithms = [tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA512]

        with self.assertRaises(tls.AlertHandshakeFailure) as cm:
            self._server_fail_hello(client, server)
        self.assertEqual(str(cm.exception), "No supported signature algorithm")

    def test_server_unsupported_version(self):
        client = self.create_client()
        client._supported_versions = [tls.TLS_VERSION_1_2]

        server = self.create_server()

        with self.assertRaises(tls.AlertHandshakeFailure) as cm:
            self._server_fail_hello(client, server)
        self.assertEqual(str(cm.exception), "No supported protocol version")

    def test_handshake(self):
        client = self.create_client()
        server = self.create_server()

        # send client hello
        client_buf = create_buffers()
        client.handle_message(b"", client_buf)
        self.assertEqual(client.state, State.CLIENT_EXPECT_SERVER_HELLO)
        server_input = merge_buffers(client_buf)
        self.assertEqual(len(server_input), 246)
        reset_buffers(client_buf)

        # handle client hello
        # send server hello, encrypted extensions, certificate, certificate verify, finished
        server_buf = create_buffers()
        server.handle_message(server_input, server_buf)
        self.assertEqual(server.state, State.SERVER_EXPECT_FINISHED)
        client_input = merge_buffers(server_buf)
        self.assertEqual(len(client_input), 2227)
        reset_buffers(server_buf)

        # handle server hello, encrypted extensions, certificate, certificate verify, finished
        # send finished
        client.handle_message(client_input, client_buf)
        self.assertEqual(client.state, State.CLIENT_POST_HANDSHAKE)
        server_input = merge_buffers(client_buf)
        self.assertEqual(len(server_input), 52)
        reset_buffers(client_buf)

        # handle finished
        server.handle_message(server_input, server_buf)
        self.assertEqual(server.state, State.SERVER_POST_HANDSHAKE)
        client_input = merge_buffers(server_buf)
        self.assertEqual(len(client_input), 0)

        # check keys match
        self.assertEqual(client._dec_key, server._enc_key)
        self.assertEqual(client._enc_key, server._dec_key)

        # handle new session ticket
        new_session_ticket = binascii.unhexlify(
            "04000035000151809468b842000020441fc19f9eb6ea425b48989c800258495"
            "a2bc30cac3a55032a7c0822feb842eb0008002a0004ffffffff"
        )
        client.handle_message(new_session_ticket, client_buf)
        server_input = merge_buffers(client_buf)
        self.assertEqual(len(server_input), 0)


class TlsTest(TestCase):
    def test_pull_client_hello(self):
        buf = Buffer(data=load("tls_client_hello.bin"))
        hello = pull_client_hello(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(
            hello.random,
            binascii.unhexlify(
                "18b2b23bf3e44b5d52ccfe7aecbc5ff14eadc3d349fabf804d71f165ae76e7d5"
            ),
        )
        self.assertEqual(
            hello.session_id,
            binascii.unhexlify(
                "9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235"
            ),
        )
        self.assertEqual(
            hello.cipher_suites,
            [
                tls.CipherSuite.AES_256_GCM_SHA384,
                tls.CipherSuite.AES_128_GCM_SHA256,
                tls.CipherSuite.CHACHA20_POLY1305_SHA256,
            ],
        )
        self.assertEqual(hello.compression_methods, [tls.CompressionMethod.NULL])

        # extensions
        self.assertEqual(hello.alpn_protocols, None)
        self.assertEqual(hello.key_exchange_modes, [tls.KeyExchangeMode.PSK_DHE_KE])
        self.assertEqual(
            hello.key_share,
            [
                (
                    tls.Group.SECP256R1,
                    binascii.unhexlify(
                        "047bfea344467535054263b75def60cffa82405a211b68d1eb8d1d944e67aef8"
                        "93c7665a5473d032cfaf22a73da28eb4aacae0017ed12557b5791f98a1e84f15"
                        "b0"
                    ),
                )
            ],
        )
        self.assertEqual(hello.server_name, None)
        self.assertEqual(
            hello.signature_algorithms,
            [
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                tls.SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA1,
            ],
        )
        self.assertEqual(hello.supported_groups, [tls.Group.SECP256R1])
        self.assertEqual(
            hello.supported_versions,
            [
                tls.TLS_VERSION_1_3,
                tls.TLS_VERSION_1_3_DRAFT_28,
                tls.TLS_VERSION_1_3_DRAFT_27,
                tls.TLS_VERSION_1_3_DRAFT_26,
            ],
        )

        self.assertEqual(
            hello.other_extensions,
            [
                (
                    tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                    CLIENT_QUIC_TRANSPORT_PARAMETERS,
                )
            ],
        )

    def test_pull_client_hello_with_alpn(self):
        buf = Buffer(data=load("tls_client_hello_with_alpn.bin"))
        hello = pull_client_hello(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(
            hello.random,
            binascii.unhexlify(
                "ed575c6fbd599c4dfaabd003dca6e860ccdb0e1782c1af02e57bf27cb6479b76"
            ),
        )
        self.assertEqual(hello.session_id, b"")
        self.assertEqual(
            hello.cipher_suites,
            [
                tls.CipherSuite.AES_128_GCM_SHA256,
                tls.CipherSuite.AES_256_GCM_SHA384,
                tls.CipherSuite.CHACHA20_POLY1305_SHA256,
                tls.CipherSuite.EMPTY_RENEGOTIATION_INFO_SCSV,
            ],
        )
        self.assertEqual(hello.compression_methods, [tls.CompressionMethod.NULL])

        # extensions
        self.assertEqual(hello.alpn_protocols, ["h3-19"])
        self.assertEqual(hello.key_exchange_modes, [tls.KeyExchangeMode.PSK_DHE_KE])
        self.assertEqual(
            hello.key_share,
            [
                (
                    tls.Group.SECP256R1,
                    binascii.unhexlify(
                        "048842315c437bb0ce2929c816fee4e942ec5cb6db6a6b9bf622680188ebb0d4"
                        "b652e69033f71686aa01cbc79155866e264c9f33f45aa16b0dfa10a222e3a669"
                        "22"
                    ),
                )
            ],
        )
        self.assertEqual(hello.server_name, "cloudflare-quic.com")
        self.assertEqual(
            hello.signature_algorithms,
            [
                tls.SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
                tls.SignatureAlgorithm.ECDSA_SECP384R1_SHA384,
                tls.SignatureAlgorithm.ECDSA_SECP521R1_SHA512,
                tls.SignatureAlgorithm.ED25519,
                tls.SignatureAlgorithm.ED448,
                tls.SignatureAlgorithm.RSA_PSS_PSS_SHA256,
                tls.SignatureAlgorithm.RSA_PSS_PSS_SHA384,
                tls.SignatureAlgorithm.RSA_PSS_PSS_SHA512,
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA384,
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA512,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA384,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA512,
            ],
        )
        self.assertEqual(
            hello.supported_groups,
            [
                tls.Group.SECP256R1,
                tls.Group.X25519,
                tls.Group.SECP384R1,
                tls.Group.SECP521R1,
            ],
        )
        self.assertEqual(hello.supported_versions, [tls.TLS_VERSION_1_3])

        # serialize
        buf = Buffer(1000)
        push_client_hello(buf, hello)
        self.assertEqual(len(buf.data), len(load("tls_client_hello_with_alpn.bin")))

    def test_pull_client_hello_with_sni(self):
        buf = Buffer(data=load("tls_client_hello_with_sni.bin"))
        hello = pull_client_hello(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(
            hello.random,
            binascii.unhexlify(
                "987d8934140b0a42cc5545071f3f9f7f61963d7b6404eb674c8dbe513604346b"
            ),
        )
        self.assertEqual(
            hello.session_id,
            binascii.unhexlify(
                "26b19bdd30dbf751015a3a16e13bd59002dfe420b799d2a5cd5e11b8fa7bcb66"
            ),
        )
        self.assertEqual(
            hello.cipher_suites,
            [
                tls.CipherSuite.AES_256_GCM_SHA384,
                tls.CipherSuite.AES_128_GCM_SHA256,
                tls.CipherSuite.CHACHA20_POLY1305_SHA256,
            ],
        )
        self.assertEqual(hello.compression_methods, [tls.CompressionMethod.NULL])

        # extensions
        self.assertEqual(hello.alpn_protocols, None)
        self.assertEqual(hello.key_exchange_modes, [tls.KeyExchangeMode.PSK_DHE_KE])
        self.assertEqual(
            hello.key_share,
            [
                (
                    tls.Group.SECP256R1,
                    binascii.unhexlify(
                        "04b62d70f907c814cd65d0f73b8b991f06b70c77153f548410a191d2b19764a2"
                        "ecc06065a480efa9e1f10c8da6e737d5bfc04be3f773e20a0c997f51b5621280"
                        "40"
                    ),
                )
            ],
        )
        self.assertEqual(hello.server_name, "cloudflare-quic.com")
        self.assertEqual(
            hello.signature_algorithms,
            [
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                tls.SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA1,
            ],
        )
        self.assertEqual(hello.supported_groups, [tls.Group.SECP256R1])
        self.assertEqual(
            hello.supported_versions,
            [
                tls.TLS_VERSION_1_3,
                tls.TLS_VERSION_1_3_DRAFT_28,
                tls.TLS_VERSION_1_3_DRAFT_27,
                tls.TLS_VERSION_1_3_DRAFT_26,
            ],
        )

        self.assertEqual(
            hello.other_extensions,
            [
                (
                    tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                    CLIENT_QUIC_TRANSPORT_PARAMETERS,
                )
            ],
        )

        # serialize
        buf = Buffer(1000)
        push_client_hello(buf, hello)
        self.assertEqual(buf.data, load("tls_client_hello_with_sni.bin"))

    def test_push_client_hello(self):
        hello = ClientHello(
            random=binascii.unhexlify(
                "18b2b23bf3e44b5d52ccfe7aecbc5ff14eadc3d349fabf804d71f165ae76e7d5"
            ),
            session_id=binascii.unhexlify(
                "9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235"
            ),
            cipher_suites=[
                tls.CipherSuite.AES_256_GCM_SHA384,
                tls.CipherSuite.AES_128_GCM_SHA256,
                tls.CipherSuite.CHACHA20_POLY1305_SHA256,
            ],
            compression_methods=[tls.CompressionMethod.NULL],
            key_exchange_modes=[tls.KeyExchangeMode.PSK_DHE_KE],
            key_share=[
                (
                    tls.Group.SECP256R1,
                    binascii.unhexlify(
                        "047bfea344467535054263b75def60cffa82405a211b68d1eb8d1d944e67aef8"
                        "93c7665a5473d032cfaf22a73da28eb4aacae0017ed12557b5791f98a1e84f15"
                        "b0"
                    ),
                )
            ],
            signature_algorithms=[
                tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
                tls.SignatureAlgorithm.ECDSA_SECP256R1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA256,
                tls.SignatureAlgorithm.RSA_PKCS1_SHA1,
            ],
            supported_groups=[tls.Group.SECP256R1],
            supported_versions=[
                tls.TLS_VERSION_1_3,
                tls.TLS_VERSION_1_3_DRAFT_28,
                tls.TLS_VERSION_1_3_DRAFT_27,
                tls.TLS_VERSION_1_3_DRAFT_26,
            ],
            other_extensions=[
                (
                    tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                    CLIENT_QUIC_TRANSPORT_PARAMETERS,
                )
            ],
        )

        buf = Buffer(1000)
        push_client_hello(buf, hello)
        self.assertEqual(buf.data, load("tls_client_hello.bin"))

    def test_pull_server_hello(self):
        buf = Buffer(data=load("tls_server_hello.bin"))
        hello = pull_server_hello(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(
            hello.random,
            binascii.unhexlify(
                "ada85271d19680c615ea7336519e3fdf6f1e26f3b1075ee1de96ffa8884e8280"
            ),
        )
        self.assertEqual(
            hello.session_id,
            binascii.unhexlify(
                "9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235"
            ),
        )
        self.assertEqual(hello.cipher_suite, tls.CipherSuite.AES_256_GCM_SHA384)
        self.assertEqual(hello.compression_method, tls.CompressionMethod.NULL)
        self.assertEqual(
            hello.key_share,
            (
                tls.Group.SECP256R1,
                binascii.unhexlify(
                    "048b27d0282242d84b7fcc02a9c4f13eca0329e3c7029aa34a33794e6e7ba189"
                    "5cca1c503bf0378ac6937c354912116ff3251026bca1958d7f387316c83ae6cf"
                    "b2"
                ),
            ),
        )
        self.assertEqual(hello.supported_version, tls.TLS_VERSION_1_3)

    def test_push_server_hello(self):
        hello = ServerHello(
            random=binascii.unhexlify(
                "ada85271d19680c615ea7336519e3fdf6f1e26f3b1075ee1de96ffa8884e8280"
            ),
            session_id=binascii.unhexlify(
                "9aee82a2d186c1cb32a329d9dcfe004a1a438ad0485a53c6bfcf55c132a23235"
            ),
            cipher_suite=tls.CipherSuite.AES_256_GCM_SHA384,
            compression_method=tls.CompressionMethod.NULL,
            key_share=(
                tls.Group.SECP256R1,
                binascii.unhexlify(
                    "048b27d0282242d84b7fcc02a9c4f13eca0329e3c7029aa34a33794e6e7ba189"
                    "5cca1c503bf0378ac6937c354912116ff3251026bca1958d7f387316c83ae6cf"
                    "b2"
                ),
            ),
            supported_version=tls.TLS_VERSION_1_3,
        )

        buf = Buffer(1000)
        push_server_hello(buf, hello)
        self.assertEqual(buf.data, load("tls_server_hello.bin"))

    def test_pull_new_session_ticket(self):
        buf = Buffer(data=load("tls_new_session_ticket.bin"))
        new_session_ticket = pull_new_session_ticket(buf)
        self.assertIsNotNone(new_session_ticket)
        self.assertTrue(buf.eof())

        self.assertEqual(new_session_ticket.lifetime_hint, 86400)
        self.assertEqual(len(new_session_ticket.ticket), 49)

    def test_pull_encrypted_extensions(self):
        buf = Buffer(data=load("tls_encrypted_extensions.bin"))
        extensions = pull_encrypted_extensions(buf)
        self.assertIsNotNone(extensions)
        self.assertTrue(buf.eof())

        self.assertEqual(
            extensions.other_extensions,
            [
                (
                    tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                    SERVER_QUIC_TRANSPORT_PARAMETERS,
                )
            ],
        )

    def test_push_encrypted_extensions(self):
        extensions = EncryptedExtensions(
            other_extensions=[
                (
                    tls.ExtensionType.QUIC_TRANSPORT_PARAMETERS,
                    SERVER_QUIC_TRANSPORT_PARAMETERS,
                )
            ]
        )

        buf = Buffer(100)
        push_encrypted_extensions(buf, extensions)
        self.assertEqual(buf.data, load("tls_encrypted_extensions.bin"))

    def test_pull_certificate(self):
        buf = Buffer(data=load("tls_certificate.bin"))
        certificate = pull_certificate(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(certificate.request_context, b"")
        self.assertEqual(certificate.certificates, [(CERTIFICATE_DATA, b"")])

    def test_push_certificate(self):
        certificate = Certificate(
            request_context=b"", certificates=[(CERTIFICATE_DATA, b"")]
        )

        buf = Buffer(1600)
        push_certificate(buf, certificate)
        self.assertEqual(buf.data, load("tls_certificate.bin"))

    def test_pull_certificate_verify(self):
        buf = Buffer(data=load("tls_certificate_verify.bin"))
        verify = pull_certificate_verify(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(verify.algorithm, tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256)
        self.assertEqual(verify.signature, CERTIFICATE_VERIFY_SIGNATURE)

    def test_push_certificate_verify(self):
        verify = CertificateVerify(
            algorithm=tls.SignatureAlgorithm.RSA_PSS_RSAE_SHA256,
            signature=CERTIFICATE_VERIFY_SIGNATURE,
        )

        buf = Buffer(400)
        push_certificate_verify(buf, verify)
        self.assertEqual(buf.data, load("tls_certificate_verify.bin"))

    def test_pull_finished(self):
        buf = Buffer(data=load("tls_finished.bin"))
        finished = pull_finished(buf)
        self.assertTrue(buf.eof())

        self.assertEqual(
            finished.verify_data,
            binascii.unhexlify(
                "f157923234ff9a4921aadb2e0ec7b1a30fce73fb9ec0c4276f9af268f408ec68"
            ),
        )

    def test_push_finished(self):
        finished = Finished(
            verify_data=binascii.unhexlify(
                "f157923234ff9a4921aadb2e0ec7b1a30fce73fb9ec0c4276f9af268f408ec68"
            )
        )

        buf = Buffer(128)
        push_finished(buf, finished)
        self.assertEqual(buf.data, load("tls_finished.bin"))
