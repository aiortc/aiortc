import json

from aiortc import RTCSessionDescription


class CopyAndPasteSignaling:
    async def receive(self):
        print('-- Please enter remote description --')
        descr_dict = json.loads(input())
        print()
        return RTCSessionDescription(
            sdp=descr_dict['sdp'],
            type=descr_dict['type'])

    async def send(self, descr):
        print('-- Your description --')
        print(json.dumps({
            'sdp': descr.sdp,
            'type': descr.type
        }))
        print()

