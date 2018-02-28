import asyncio


class MediaStreamTrack:
    pass


class AudioStreamTrack(MediaStreamTrack):
    """
    The base implementation just reads silence.

    Subclass it to provide a useful implementation.
    """
    kind = 'audio'

    async def recv(self):
        await asyncio.sleep(0.02)
        return b'\x00' * 160


class VideoStreamTrack(MediaStreamTrack):
    kind = 'video'

    async def recv(self):
        raise NotImplementedError
