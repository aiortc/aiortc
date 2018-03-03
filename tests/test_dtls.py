import asyncio
import logging
from unittest import TestCase

from aiortc import dtls

from .utils import dummy_transport_pair, run


class DtlsSrtpTest(TestCase):
    def test_connect(self):
        transport1, transport2 = dummy_transport_pair()

        context1 = dtls.DtlsSrtpContext()
        session1 = dtls.DtlsSrtpSession(
            context=context1, transport=transport1, is_server=True)

        context2 = dtls.DtlsSrtpContext()
        session2 = dtls.DtlsSrtpSession(
            context=context2, transport=transport2, is_server=False)

        session1.remote_fingerprint = session2.local_fingerprint
        session2.remote_fingerprint = session1.local_fingerprint
        run(asyncio.gather(session1.connect(), session2.connect()))

        # send encypted data
        run(session1.data.send(b'ping'))
        data = run(session2.data.recv())
        self.assertEqual(data, b'ping')

        run(session2.data.send(b'pong'))
        data = run(session1.data.recv())
        self.assertEqual(data, b'pong')

        # shutdown
        run(session1.close())
        run(session2.close())


logging.basicConfig(level=logging.DEBUG)
