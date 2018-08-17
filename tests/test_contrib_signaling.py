import argparse
import asyncio
from unittest import TestCase

from aiortc import RTCSessionDescription
from aiortc.contrib.signaling import add_signaling_arguments, create_signaling

from .utils import run


async def delay(coro):
    await asyncio.sleep(0.1)
    return await coro()


offer = RTCSessionDescription(sdp='some-offer', type='offer')
answer = RTCSessionDescription(sdp='some-answer', type='answer')


class SignalingTest(TestCase):
    def test_copy_and_paste(self):
        captured_sdp = None

        def mock_input():
            return captured_sdp

        def mock_print(v=''):
            nonlocal captured_sdp
            if v.startswith('{'):
                captured_sdp = v

        # hijack builtins
        original_input = __builtins__['input']
        original_print = __builtins__['print']
        __builtins__['input'] = mock_input
        __builtins__['print'] = mock_print

        parser = argparse.ArgumentParser()
        add_signaling_arguments(parser)
        args = parser.parse_args(['-s', 'copy-and-paste'])

        sig_server = create_signaling(args)
        sig_client = create_signaling(args)

        res = run(asyncio.gather(sig_server.send(offer), delay(sig_client.receive)))
        self.assertEqual(res[1], offer)

        res = run(asyncio.gather(sig_client.send(answer), delay(sig_server.receive)))
        self.assertEqual(res[1], answer)

        asyncio.gather(sig_server.close(), sig_client.close())

        # restore builtins
        __builtins__['input'] = original_input
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

        asyncio.gather(sig_server.close(), sig_client.close())

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

        asyncio.gather(sig_server.close(), sig_client.close())
