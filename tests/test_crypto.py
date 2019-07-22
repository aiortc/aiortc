import binascii
import os
from unittest import TestCase, skipIf

from aioquic.buffer import Buffer
from aioquic.quic.crypto import (
    INITIAL_CIPHER_SUITE,
    CryptoError,
    CryptoPair,
    derive_key_iv_hp,
)
from aioquic.quic.packet import PACKET_FIXED_BIT
from aioquic.tls import CipherSuite

CHACHA20_CLIENT_PACKET_NUMBER = 2
CHACHA20_CLIENT_PLAIN_HEADER = binascii.unhexlify(
    "e1ff000014f5b06e20f064d8783dfab56c61e5e16e8024c0e1d6ddc2a43565a240170002"
)
CHACHA20_CLIENT_PLAIN_PAYLOAD = binascii.unhexlify("0200000000")
CHACHA20_CLIENT_ENCRYPTED_PACKET = binascii.unhexlify(
    "e7ff000014f5b06e20f064d8783dfab56c61e5e16e8024c0e1d6ddc2a43565a240175554"
    "c9ead500f378c5b1dd3eebab26c089128698919bee"
)

LONG_CLIENT_PACKET_NUMBER = 2
LONG_CLIENT_PLAIN_HEADER = binascii.unhexlify(
    "c3ff000012508394c8f03e51570800449f00000002"
)
LONG_CLIENT_PLAIN_PAYLOAD = binascii.unhexlify(
    "060040c4010000c003036660261ff947cea49cce6cfad687f457cf1b14531ba1"
    "4131a0e8f309a1d0b9c4000006130113031302010000910000000b0009000006"
    "736572766572ff01000100000a00140012001d00170018001901000101010201"
    "03010400230000003300260024001d00204cfdfcd178b784bf328cae793b136f"
    "2aedce005ff183d7bb1495207236647037002b0003020304000d0020001e0403"
    "05030603020308040805080604010501060102010402050206020202002d0002"
    "0101001c00024001"
) + bytes(963)
LONG_CLIENT_ENCRYPTED_PACKET = binascii.unhexlify(
    "c2ff000012508394c8f03e51570800449f9bd343fd65f354ebb400418b614f73"
    "765009c0162d594777f9e6ddeb32fba3865cffd7e26e3724d4997cdde8df34f8"
    "868772fed2412d43046f44dc7c6adf5ee10da456d56c892c8f69594594e8dcab"
    "edb10d591130ca464588f2834eab931b10feb963c1947a05f57062692c242248"
    "ad0133b31f6dcc585ba344ca5beb382fb619272e65dfccae59c08eb00b7d2a5b"
    "bccd888582df1d1aee040aea76ab4dfdcae126791e71561b1f58312edb31c164"
    "ff1341fd2820e2399946bad901e425dae58a9859ef1825e7d757a6291d9ba6ee"
    "1a8c836dc0027cd705bd2bc67f56bad0024efaa3819cbb5d46cefdb7e0df3ad9"
    "2b0689650e2b49ac29e6398bedc755541a3f3865bc4759bec74d721a28a0452c"
    "1260189e8e92f844c91b27a00fc5ed6d14d8fceb5a848bea0a3208162c7a9578"
    "2fcf9a045b20b76710a2565372f2541181030e4350e199e62fa4e2e0bba19ff6"
    "6662ab8cc6815eeaa20b80d5f31c41e551f558d2c836a215ccff4e8afd2fec4b"
    "fcb9ea9d051d12162f1b14842489b69d72a307d9144fced64fc4aa21ebd310f8"
    "97cf00062e90dad5dbf04186622e6c1296d388176585fdb395358ecfec4d95db"
    "4429f4473a76210866fd180eaeb60da433500c74c00aef24d77eae81755faa03"
    "e71a8879937b32d31be2ba51d41b5d7a1fbb4d952b10dd2d6ec171a3187cf3f6"
    "4d520afad796e4188bc32d153241c083f225b6e6b845ce9911bd3fe1eb4737b7"
    "1c8d55e3962871b73657b1e2cce368c7400658d47cfd9290ed16cdc2a6e3e7dc"
    "ea77fb5c6459303a32d58f62969d8f4670ce27f591c7a59cc3e7556eda4c58a3"
    "2e9f53fd7f9d60a9c05cd6238c71e3c82d2efabd3b5177670b8d595151d7eb44"
    "aa401fe3b5b87bdb88dffb2bfb6d1d0d8868a41ba96265ca7a68d06fc0b74bcc"
    "ac55b038f8362b84d47f52744323d08b46bfec8c421f991e1394938a546a7482"
    "a17c72be109ea4b0c71abc7d9c0ac0960327754e1043f18a32b9fb402fc33fdc"
    "b6a0b4fdbbddbdf0d85779879e98ef211d104a5271f22823f16942cfa8ace68d"
    "0c9e5b52297da9702d8f1de24bcd06284ac8aa1068fa21a82abbca7e7454b848"
    "d7de8c3d43560541a362ff4f6be06c0115e3a733bff44417da11ae668857bba2"
    "c53ba17db8c100f1b5c7c9ea960d3f3d3b9e77c16c31a222b498a7384e286b9b"
    "7c45167d5703de715f9b06708403562dcff77fdf2793f94e294888cebe8da4ee"
    "88a53e38f2430addc161e8b2e2f2d40541d10cda9a7aa518ac14d0195d8c2012"
    "0b4f1d47d6d0909e69c4a0e641b83c1ad4fff85af4751035bc5698b6141ecc3f"
    "bffcf2f55036880071ba1189274007967f64468172854d140d229320d689f576"
    "60f6c445e629d15ff2dcdff4b71a41ec0c24bd2fd8f5ad13b2c3688e0fdb8dbc"
    "ce42e6cf49cf60d022ccd5b19b4fd5d98dc10d9ce3a626851b1fdd23e1fa3a96"
    "1f9b0333ab8d632e48c944b82bdd9e800fa2b2b9e31e96aee54b40edaf6b79ec"
    "211fdc95d95ef552aa532583d76a539e988e416a0a10df2550cdeacafc3d61b0"
    "b0a79337960a0be8cf6169e4d55fa6e7a9c2e8efabab3da008f5bcc38c1bbabd"
    "b6c10368723da0ae83c4b1819ff54946e7806458d80d7be2c867d46fe1f029c5"
    "8625313cf481f9541345f17eb544901f"
)

LONG_SERVER_PACKET_NUMBER = 1
LONG_SERVER_PLAIN_HEADER = binascii.unhexlify("c1ff00001205f067a5502a4262b50040740001")
LONG_SERVER_PLAIN_PAYLOAD = binascii.unhexlify(
    "0d0000000018410a020000560303eefce7f7b37ba1d1632e96677825ddf73988"
    "cfc79825df566dc5430b9a045a1200130100002e00330024001d00209d3c940d"
    "89690b84d08a60993c144eca684d1081287c834d5311bcf32bb9da1a002b0002"
    "0304"
)
LONG_SERVER_ENCRYPTED_PACKET = binascii.unhexlify(
    "caff00001205f067a5502a4262b5004074d74b7e486176fa3b713f272a9bf03e"
    "e28d3c8addb4e805b3a110b663122a75eee93c9177ac6b7a6b548e15a7b8f884"
    "65e9eab253a760779b2e6a2c574882b48d3a3eed696e50d04d5ec59af85261e4"
    "cdbe264bd65f2b076760c69beef23aa714c9a174d6feeaf8c677cafb7486a838"
    "61b0dd273a"
)

SHORT_SERVER_PACKET_NUMBER = 3
SHORT_SERVER_PLAIN_HEADER = binascii.unhexlify("41b01fd24a586a9cf30003")
SHORT_SERVER_PLAIN_PAYLOAD = binascii.unhexlify(
    "06003904000035000151805a4bebf5000020b098c8dc4183e4c182572e10ac3e"
    "2b88897e0524c8461847548bd2dffa2c0ae60008002a0004ffffffff"
)
SHORT_SERVER_ENCRYPTED_PACKET = binascii.unhexlify(
    "5db01fd24a586a9cf33dec094aaec6d6b4b7a5e15f5a3f05d06cf1ad0355c19d"
    "cce0807eecf7bf1c844a66e1ecd1f74b2a2d69bfd25d217833edd973246597bd"
    "5107ea15cb1e210045396afa602fe23432f4ab24ce251b"
)


class CryptoTest(TestCase):
    """
    Test vectors from:

    https://tools.ietf.org/html/draft-ietf-quic-tls-18#appendix-A
    """

    def create_crypto(self, is_client):
        pair = CryptoPair()
        pair.setup_initial(
            cid=binascii.unhexlify("8394c8f03e515708"), is_client=is_client
        )
        return pair

    def test_derive_key_iv_hp(self):
        # client
        secret = binascii.unhexlify(
            "8a3515a14ae3c31b9c2d6d5bc58538ca5cd2baa119087143e60887428dcb52f6"
        )
        key, iv, hp = derive_key_iv_hp(INITIAL_CIPHER_SUITE, secret)
        self.assertEqual(key, binascii.unhexlify("98b0d7e5e7a402c67c33f350fa65ea54"))
        self.assertEqual(iv, binascii.unhexlify("19e94387805eb0b46c03a788"))
        self.assertEqual(hp, binascii.unhexlify("0edd982a6ac527f2eddcbb7348dea5d7"))

        # server
        secret = binascii.unhexlify(
            "47b2eaea6c266e32c0697a9e2a898bdf5c4fb3e5ac34f0e549bf2c58581a3811"
        )
        key, iv, hp = derive_key_iv_hp(INITIAL_CIPHER_SUITE, secret)
        self.assertEqual(key, binascii.unhexlify("9a8be902a9bdd91d16064ca118045fb4"))
        self.assertEqual(iv, binascii.unhexlify("0a82086d32205ba22241d8dc"))
        self.assertEqual(hp, binascii.unhexlify("94b9452d2b3c7c7f6da7fdd8593537fd"))

    @skipIf(os.environ.get("TRAVIS") == "true", "travis lacks a modern OpenSSL")
    def test_decrypt_chacha20(self):
        pair = CryptoPair()
        pair.recv.setup(
            CipherSuite.CHACHA20_POLY1305_SHA256,
            binascii.unhexlify(
                "fcc211ac3ff1f3fe1b096a41e701e30033cbd899494ebabbbc009ee2626d987e"
            ),
        )

        plain_header, plain_payload, packet_number = pair.decrypt_packet(
            CHACHA20_CLIENT_ENCRYPTED_PACKET, 34, 0
        )
        self.assertEqual(plain_header, CHACHA20_CLIENT_PLAIN_HEADER)
        self.assertEqual(plain_payload, CHACHA20_CLIENT_PLAIN_PAYLOAD)
        self.assertEqual(packet_number, CHACHA20_CLIENT_PACKET_NUMBER)

    def test_decrypt_long_client(self):
        pair = self.create_crypto(is_client=False)

        plain_header, plain_payload, packet_number = pair.decrypt_packet(
            LONG_CLIENT_ENCRYPTED_PACKET, 17, 0
        )
        self.assertEqual(plain_header, LONG_CLIENT_PLAIN_HEADER)
        self.assertEqual(plain_payload, LONG_CLIENT_PLAIN_PAYLOAD)
        self.assertEqual(packet_number, LONG_CLIENT_PACKET_NUMBER)

    def test_decrypt_long_server(self):
        pair = self.create_crypto(is_client=True)

        plain_header, plain_payload, packet_number = pair.decrypt_packet(
            LONG_SERVER_ENCRYPTED_PACKET, 17, 0
        )
        self.assertEqual(plain_header, LONG_SERVER_PLAIN_HEADER)
        self.assertEqual(plain_payload, LONG_SERVER_PLAIN_PAYLOAD)
        self.assertEqual(packet_number, LONG_SERVER_PACKET_NUMBER)

    def test_decrypt_no_key(self):
        pair = CryptoPair()
        with self.assertRaises(CryptoError):
            pair.decrypt_packet(LONG_SERVER_ENCRYPTED_PACKET, 17, 0)

    def test_decrypt_short_server(self):
        pair = CryptoPair()
        pair.recv.setup(
            INITIAL_CIPHER_SUITE,
            binascii.unhexlify(
                "310281977cb8c1c1c1212d784b2d29e5a6489e23de848d370a5a2f9537f3a100"
            ),
        )

        plain_header, plain_payload, packet_number = pair.decrypt_packet(
            SHORT_SERVER_ENCRYPTED_PACKET, 9, 0
        )
        self.assertEqual(plain_header, SHORT_SERVER_PLAIN_HEADER)
        self.assertEqual(plain_payload, SHORT_SERVER_PLAIN_PAYLOAD)
        self.assertEqual(packet_number, SHORT_SERVER_PACKET_NUMBER)

    @skipIf(os.environ.get("TRAVIS") == "true", "travis lacks a modern OpenSSL")
    def test_encrypt_chacha20(self):
        pair = CryptoPair()
        pair.send.setup(
            CipherSuite.CHACHA20_POLY1305_SHA256,
            binascii.unhexlify(
                "fcc211ac3ff1f3fe1b096a41e701e30033cbd899494ebabbbc009ee2626d987e"
            ),
        )

        packet = pair.encrypt_packet(
            CHACHA20_CLIENT_PLAIN_HEADER,
            CHACHA20_CLIENT_PLAIN_PAYLOAD,
            CHACHA20_CLIENT_PACKET_NUMBER,
        )
        self.assertEqual(packet, CHACHA20_CLIENT_ENCRYPTED_PACKET)

    def test_encrypt_long_client(self):
        pair = self.create_crypto(is_client=True)

        packet = pair.encrypt_packet(
            LONG_CLIENT_PLAIN_HEADER,
            LONG_CLIENT_PLAIN_PAYLOAD,
            LONG_CLIENT_PACKET_NUMBER,
        )
        self.assertEqual(packet, LONG_CLIENT_ENCRYPTED_PACKET)

    def test_encrypt_long_server(self):
        pair = self.create_crypto(is_client=False)

        packet = pair.encrypt_packet(
            LONG_SERVER_PLAIN_HEADER,
            LONG_SERVER_PLAIN_PAYLOAD,
            LONG_SERVER_PACKET_NUMBER,
        )
        self.assertEqual(packet, LONG_SERVER_ENCRYPTED_PACKET)

    def test_encrypt_short_server(self):
        pair = CryptoPair()
        pair.send.setup(
            INITIAL_CIPHER_SUITE,
            binascii.unhexlify(
                "310281977cb8c1c1c1212d784b2d29e5a6489e23de848d370a5a2f9537f3a100"
            ),
        )

        packet = pair.encrypt_packet(
            SHORT_SERVER_PLAIN_HEADER,
            SHORT_SERVER_PLAIN_PAYLOAD,
            SHORT_SERVER_PACKET_NUMBER,
        )
        self.assertEqual(packet, SHORT_SERVER_ENCRYPTED_PACKET)

    def test_key_update(self):
        pair1 = self.create_crypto(is_client=True)
        pair2 = self.create_crypto(is_client=False)

        def create_packet(key_phase, packet_number):
            buf = Buffer(capacity=100)
            buf.push_uint8(PACKET_FIXED_BIT | key_phase << 2 | 1)
            buf.push_bytes(binascii.unhexlify("8394c8f03e515708"))
            buf.push_uint16(packet_number)
            return buf.data, b"\x00\x01\x02\x03"

        def send(sender, receiver, packet_number=0):
            plain_header, plain_payload = create_packet(
                key_phase=sender.key_phase, packet_number=packet_number
            )
            encrypted = sender.encrypt_packet(
                plain_header, plain_payload, packet_number
            )
            recov_header, recov_payload, recov_packet_number = receiver.decrypt_packet(
                encrypted, len(plain_header) - 2, 0
            )
            self.assertEqual(recov_header, plain_header)
            self.assertEqual(recov_payload, plain_payload)
            self.assertEqual(recov_packet_number, packet_number)

        # roundtrip
        send(pair1, pair2, 0)
        send(pair2, pair1, 0)
        self.assertEqual(pair1.key_phase, 0)
        self.assertEqual(pair2.key_phase, 0)

        # pair 1 key update
        pair1.update_key()

        # roundtrip
        send(pair1, pair2, 1)
        send(pair2, pair1, 1)
        self.assertEqual(pair1.key_phase, 1)
        self.assertEqual(pair2.key_phase, 1)

        # pair 2 key update
        pair2.update_key()

        # roundtrip
        send(pair2, pair1, 2)
        send(pair1, pair2, 2)
        self.assertEqual(pair1.key_phase, 0)
        self.assertEqual(pair2.key_phase, 0)

        # pair 1 key - update, but not next to send
        pair1.update_key()

        # roundtrip
        send(pair2, pair1, 3)
        send(pair1, pair2, 3)
        self.assertEqual(pair1.key_phase, 1)
        self.assertEqual(pair2.key_phase, 1)
