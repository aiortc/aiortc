from dataclasses import dataclass

from .mediastreams import MediaStreamTrack
from .rtcrtpreceiver import RTCRtpReceiver
from .rtcrtptransceiver import RTCRtpTransceiver


@dataclass
class RTCTrackEvent:
    """
    This event is fired on :class:`RTCPeerConnection` when a new
    :class:`MediaStreamTrack` is added by the remote party.
    """

    receiver: RTCRtpReceiver
    "The :class:`RTCRtpReceiver` associated with the event."
    track: MediaStreamTrack
    "The :class:`MediaStreamTrack` associated with the event."
    transceiver: RTCRtpTransceiver
    "The :class:`RTCRtpTransceiver` associated with the event."
