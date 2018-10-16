import argparse
import asyncio
from unittest import TestCase

from aiortc import RTCIceCandidate, RTCSessionDescription
from aiortc.contrib.signaling import (add_signaling_arguments,
                                      create_signaling, object_from_string,
                                      object_to_string)

from .utils import run


async def delay(coro):
    await asyncio.sleep(0.1)
    return await coro()


offer = RTCSessionDescription(sdp='some-offer', type='offer')
answer = RTCSessionDescription(sdp='some-answer', type='answer')


class SignalingTest(TestCase):
    def test_copy_and_paste(self):
        def mock_print(v=''):
            pass

        # hijack builtins
        original_print = __builtins__['print']
        __builtins__['print'] = mock_print

        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(['-s', 'copy-and-paste'])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        class MockReader:
            def __init__(self, queue):
                self.queue = queue

            async def readline(self):
                return await self.queue.get()

        class MockWritePipe:
            def __init__(self, queue):
                self.queue = queue

            def write(self, msg):
                asyncio.ensure_future(self.queue.put(msg))

        def dummy_stdio():
            queue = asyncio.Queue()
            return MockReader(queue), MockWritePipe(queue)

        # mock out reader / write pipe
        run(sig_server._connect())
        run(sig_client._connect())
        sig_server._reader, sig_client._write_pipe = dummy_stdio()
        sig_client._reader, sig_server._write_pipe = dummy_stdio()

        res = run(asyncio.gather(sig_server.send(offer), delay(sig_client.receive)))
        self.assertEqual(res[1], offer)

        res = run(asyncio.gather(sig_client.send(answer), delay(sig_server.receive)))
        self.assertEqual(res[1], answer)

        run(asyncio.gather(sig_server.close(), sig_client.close()))

        # restore builtins
        __builtins__['print'] = original_print

    def test_tcp_socket(self):
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(['-s', 'tcp-socket'])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        res = run(asyncio.gather(sig_server.send(offer), delay(sig_client.receive)))
        self.assertEqual(res[1], offer)

        res = run(asyncio.gather(sig_client.send(answer), delay(sig_server.receive)))
        self.assertEqual(res[1], answer)

        run(asyncio.gather(sig_server.close(), sig_client.close()))

    def test_tcp_socket_abrupt_disconnect(self):
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(['-s', 'tcp-socket'])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        res = run(asyncio.gather(sig_server.send(offer), delay(sig_client.receive)))
        self.assertEqual(res[1], offer)

        # break connection
        sig_client._writer.close()
        sig_server._writer.close()

        res = run(sig_server.receive())
        self.assertIsNone(res)

        res = run(sig_client.receive())
        self.assertIsNone(res)

        run(asyncio.gather(sig_server.close(), sig_client.close()))

    def test_unix_socket(self):
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(['-s', 'unix-socket'])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        res = run(asyncio.gather(sig_server.send(offer), delay(sig_client.receive)))
        self.assertEqual(res[1], offer)

        res = run(asyncio.gather(sig_client.send(answer), delay(sig_server.receive)))
        self.assertEqual(res[1], answer)

        run(asyncio.gather(sig_server.close(), sig_client.close()))

    def test_unix_socket_abrupt_disconnect(self):
        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(['-s', 'unix-socket'])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        res = run(asyncio.gather(sig_server.send(offer), delay(sig_client.receive)))
        self.assertEqual(res[1], offer)

        # break connection
        sig_client._writer.close()
        sig_server._writer.close()

        res = run(sig_server.receive())
        self.assertIsNone(res)

        res = run(sig_client.receive())
        self.assertIsNone(res)

        run(asyncio.gather(sig_server.close(), sig_client.close()))


class SignalingUtilsTest(TestCase):
    def test_bye_from_string(self):
        self.assertEqual(object_from_string('{"type": "bye"}'), None)

    def test_bye_to_string(self):
        self.assertEqual(object_to_string(None), '{"type": "bye"}')

    def test_candidate_from_string(self):
        candidate = object_from_string(
            '{"candidate": "candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ host", "id": "audio", "label": 0, "type": "candidate"}')  # noqa
        self.assertEqual(candidate.component, 1)
        self.assertEqual(candidate.foundation, '0')
        self.assertEqual(candidate.ip, '192.168.99.7')
        self.assertEqual(candidate.port, 33543)
        self.assertEqual(candidate.priority, 2122252543)
        self.assertEqual(candidate.protocol, 'UDP')
        self.assertEqual(candidate.sdpMid, 'audio')
        self.assertEqual(candidate.sdpMLineIndex, 0)
        self.assertEqual(candidate.type, 'host')

    def test_candidate_to_string(self):
        candidate = RTCIceCandidate(
            component=1,
            foundation='0',
            ip='192.168.99.7',
            port=33543,
            priority=2122252543,
            protocol='UDP',
            type='host')
        candidate.sdpMid = 'audio'
        candidate.sdpMLineIndex = 0
        self.assertEqual(
            object_to_string(candidate),
            '{"candidate": "candidate:0 1 UDP 2122252543 192.168.99.7 33543 typ host", "id": "audio", "label": 0, "type": "candidate"}')  # noqa
