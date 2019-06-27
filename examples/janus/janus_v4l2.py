# janus_v4l2.py
# To demonstrate connecting to a Janus videoroom and writing the first
# participant video stream to a v4l2loopback dummy device (e.g. /dev/video2),
# where it can be tested by doing "ffplay -i /dev/video2" from another window.

import argparse
import asyncio
import logging
import random
import string
import time

import aiohttp

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaBlackhole, MediaPlayer, MediaRecorder


def transaction_id():
    return "".join(random.choice(string.ascii_letters) for x in range(12))


class JanusPlugin:
    def __init__(self, session, url, plugin_id):
        self._queue = asyncio.Queue()
        self._session = session
        self._url = url + "/" + str(plugin_id)
        self.plugin_id = plugin_id

    async def send(self, payload):
        message = {"janus": "message", "transaction": transaction_id()}
        message.update(payload)
        async with self._session._http.post(self._url, json=message) as response:
            data = await response.json()
            assert data["janus"] == "ack"

        response = await self._queue.get()
        assert response["transaction"] == message["transaction"]
        return response


class JanusSession:
    def __init__(self, url):
        self._http = None
        self._poll_task = None
        self._plugins = {}
        self._root_url = url
        self._session_url = None

    async def attach(self, plugin):
        message = {"janus": "attach", "plugin": plugin, "transaction": transaction_id()}
        async with self._http.post(self._session_url, json=message) as response:
            data = await response.json()
            assert data["janus"] == "success"
            plugin_id = data["data"]["id"]
            plugin = JanusPlugin(self, self._session_url, plugin_id)
            self._plugins[plugin_id] = plugin
            return plugin

    async def create(self):
        self._http = aiohttp.ClientSession()
        message = {"janus": "create", "transaction": transaction_id()}
        async with self._http.post(self._root_url, json=message) as response:
            data = await response.json()
            assert data["janus"] == "success"
            session_id = data["data"]["id"]
            self._session_url = self._root_url + "/" + str(session_id)

        self._poll_task = asyncio.ensure_future(self._poll())

    async def destroy(self):
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        if self._session_url:
            message = {"janus": "destroy", "transaction": transaction_id()}
            async with self._http.post(self._session_url, json=message) as response:
                data = await response.json()
                assert data["janus"] == "success"
            self._session_url = None

        if self._http:
            await self._http.close()
            self._http = None

    async def _poll(self):
        while True:
            params = {"maxev": 1, "rid": int(time.time() * 1000)}
            async with self._http.get(self._session_url, params=params) as response:
                data = await response.json()
                if data["janus"] == "event":
                    plugin = self._plugins.get(data["sender"], None)
                    if plugin:
                        await plugin._queue.put(data)
                    else:
                        print(data)


async def subscribe(sub_id, room, recorder, session):
    plugin = await session.attach("janus.plugin.videoroom")
    pc = RTCPeerConnection()

    @pc.on("track")
    async def on_track(track):
        print("Track %s received" % track.kind)
        if track.kind == 'video':
            recorder.addTrack(track)

    request = {
        "request" : "join",
        "ptype" : "subscriber",
        "room" : room,
        "feed" : sub_id,
    }

    response = await plugin.send(
        {
            "body": request,
        }
    )

    if response['plugindata']['data'].get("error"):
        print('** [subscribe] response error')
        return

    await pc.setRemoteDescription(RTCSessionDescription(
        sdp=response["jsep"]["sdp"], type=response["jsep"]["type"]
    ))

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    response = await plugin.send(
        {
            "body": {"request" : "start"},
            "jsep": {
                "sdp": pc.localDescription.sdp,
                "trickle": False,
                "type": pc.localDescription.type,
            }
        }
    )

    await recorder.start()

async def run(pc, player, recorder, room, session):
    await session.create()

    # configure media
    media = {"audio": False, "video": True}
    if player and player.audio:
        pc.addTrack(player.audio)

    if player and player.video:
        pc.addTrack(player.video)
    else:
        pc.addTrack(VideoStreamTrack())

    # join video room
    plugin = await session.attach("janus.plugin.videoroom")
    videoroom_response = await plugin.send(
        {
            "body": {
                "display": "aiortc",
                "ptype": "publisher",
                "request": "join",
                "room": room,
            }
        }
    )

    # find out who else is in the room?
    publishers = videoroom_response['plugindata']['data']['publishers']
    for publisher in publishers:
        remote_id = publisher['id']
        remote_name = publisher['display']
        print('remote_id:', remote_id, 'name:', remote_name)

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    request = {"request": "configure"}
    request.update(media)
    response = await plugin.send(
        {
            "body": request,
            "jsep": {
                "sdp": pc.localDescription.sdp,
                "trickle": False,
                "type": pc.localDescription.type,
            },
        }
    )

    # apply answer
    answer = RTCSessionDescription(
        sdp=response["jsep"]["sdp"], type=response["jsep"]["type"]
    )
    await pc.setRemoteDescription(answer)

    # start recording from 1st remote_id participant
    if publishers != []:
        await subscribe(publishers[0]["id"], room, recorder, session)

    # exchange media for 10 minutes
    print("Exchanging media")
    await asyncio.sleep(600)
    print("--Stopped 10 minutes limit--")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Janus")
    parser.add_argument("url", help="Janus root URL, e.g. http://localhost:8088/janus")
    parser.add_argument(
        "--room",
        type=int,
        default=1234,
        help="The video room ID to join (default: 1234).",
    ),
    parser.add_argument("--play-from", help="Read the media from a file and sent it to room."),
    parser.add_argument("--record-to", help="Write the media received to a device."),
    parser.add_argument("--verbose", "-v", action="count")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # create signaling and peer connection
    session = JanusSession(args.url)
    pc = RTCPeerConnection()

    # create media source
    if args.play_from:
        player = MediaPlayer(args.play_from)
    else:
        player = None

    # create media sink
    if args.record_to:
        print('Recording to: ', args.record_to)
        recorder = MediaRecorder(args.record_to, format='v4l2')
    else:
        recorder = MediaBlackhole()


    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(
            run(pc=pc, player=player, recorder=recorder, room=args.room, session=session)
        )
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(recorder.stop())
        loop.run_until_complete(pc.close())
        loop.run_until_complete(session.destroy())
