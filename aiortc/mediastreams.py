class MediaStreamTrack:
    pass


class AudioStreamTrack(MediaStreamTrack):
    kind = 'audio'


class VideoStreamTrack(MediaStreamTrack):
    kind = 'video'
