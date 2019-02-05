import binascii
from unittest import TestCase

from aioquic.crypto import derive_keying_material


class CryptoTest(TestCase):
    """
    Test vectors from:

    https://tools.ietf.org/html/draft-ietf-quic-tls-18#appendix-A
    """

    def test_client_initial(self):
        cid = binascii.unhexlify('8394c8f03e515708')
        key, iv, hp = derive_keying_material(cid, is_client=True)
        self.assertEqual(key, binascii.unhexlify('98b0d7e5e7a402c67c33f350fa65ea54'))
        self.assertEqual(iv, binascii.unhexlify('19e94387805eb0b46c03a788'))
        self.assertEqual(hp, binascii.unhexlify('0edd982a6ac527f2eddcbb7348dea5d7'))

    def test_server_initial(self):
        cid = binascii.unhexlify('8394c8f03e515708')
        key, iv, hp = derive_keying_material(cid, is_client=False)
        self.assertEqual(key, binascii.unhexlify('9a8be902a9bdd91d16064ca118045fb4'))
        self.assertEqual(iv, binascii.unhexlify('0a82086d32205ba22241d8dc'))
        self.assertEqual(hp, binascii.unhexlify('94b9452d2b3c7c7f6da7fdd8593537fd'))
