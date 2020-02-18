import attr

from .mediastreams import MediaStreamTrack
from .rtcrtpreceiver import RTCRtpReceiver
from .rtcrtptransceiver import RTCRtpTransceiver


@attr.s
class RTCTrackEvent:
    """
    This event is fired on :class:`RTCPeerConnection` when a new
    :class:`MediaStreamTrack` is added by the remote party.
    """

    receiver: RTCRtpReceiver = attr.ib()
    "The :class:`RTCRtpReceiver` associated with the event."
    track: MediaStreamTrack = attr.ib()
    "The :class:`MediaStreamTrack` associated with the event."
    transceiver: RTCRtpTransceiver = attr.ib()
    "The :class:`RTCRtpTransceiver` associated with the event."
