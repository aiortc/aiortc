try:
    import av
    from av import AudioFrame, VideoFrame
    from av.frame import Frame
    from av.packet import Packet
except ImportError:
    av = None
    AudioFrame = None
    VideoFrame = None
    Frame = None
    Packet = None

    