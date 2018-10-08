import attr


@attr.s
class RTCTrackEvent:
    """
    This event is fired on :class:`RTCPeerConnection` when a new
    :class:`MediaStreamTrack` is added by the remote party.
    """
    receiver = attr.ib()  # type: RTCRtpReceiver
    "The :class:`RTCRtpReceiver` associated with the event."
    track = attr.ib()  # type: MediaStreamTrack
    "The :class:`MediaStreamTrack` associated with the event."
    transceiver = attr.ib()  # type: RTCRtpTransceiver
    "The :class:`RTCRtpTransceiver` associated with the event."
