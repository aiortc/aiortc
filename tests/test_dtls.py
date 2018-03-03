import asyncio
import logging
from unittest import TestCase

from aiortc.dtls import DtlsSrtpContext, DtlsSrtpSession
from aiortc.utils import first_completed

from .utils import dummy_transport_pair, run


class DtlsSrtpTest(TestCase):
    def test_connect(self):
        transport1, transport2 = dummy_transport_pair()

        context1 = DtlsSrtpContext()
        session1 = DtlsSrtpSession(
            context=context1, transport=transport1, is_server=True)

        context2 = DtlsSrtpContext()
        session2 = DtlsSrtpSession(
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
        run(asyncio.sleep(0.5))
        self.assertEqual(session1.state, DtlsSrtpSession.State.CLOSED)
        self.assertEqual(session2.state, DtlsSrtpSession.State.CLOSED)

        # try closing again
        run(session1.close())
        run(session2.close())

    def test_abrupt_disconnect(self):
        transport1, transport2 = dummy_transport_pair()

        context1 = DtlsSrtpContext()
        session1 = DtlsSrtpSession(
            context=context1, transport=transport1, is_server=True)

        context2 = DtlsSrtpContext()
        session2 = DtlsSrtpSession(
            context=context2, transport=transport2, is_server=False)

        session1.remote_fingerprint = session2.local_fingerprint
        session2.remote_fingerprint = session1.local_fingerprint
        run(asyncio.gather(session1.connect(), session2.connect()))

        # break one connection
        run(first_completed(
            session1.data.recv(),
            transport1.close(),
        ))
        run(asyncio.sleep(0))
        self.assertEqual(session1.state, DtlsSrtpSession.State.CLOSED)

        # break other connection
        run(first_completed(
            session2.data.recv(),
            transport2.close(),
        ))
        run(asyncio.sleep(0))
        self.assertEqual(session2.state, DtlsSrtpSession.State.CLOSED)

        # try closing again
        run(session1.close())
        run(session2.close())


logging.basicConfig(level=logging.DEBUG)
