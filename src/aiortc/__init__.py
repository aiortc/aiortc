# ruff: noqa: F401
import logging

from .exceptions import InvalidAccessError, InvalidStateError
from .mediastreams import (
    AudioStreamTrack,
    MediaStreamError,
    MediaStreamTrack,
    VideoStreamTrack,
)
from .rtcconfiguration import RTCBundlePolicy, RTCConfiguration, RTCIceServer
from .rtcdatachannel import RTCDataChannel, RTCDataChannelParameters
from .rtcdtlstransport import (
    RTCCertificate,
    RTCDtlsFingerprint,
    RTCDtlsParameters,
    RTCDtlsTransport,
)
from .rtcicetransport import (
    RTCIceCandidate,
    RTCIceGatherer,
    RTCIceParameters,
    RTCIceTransport,
)
from .rtcpeerconnection import RTCPeerConnection
from .rtcrtpparameters import (
    RTCRtcpParameters,
    RTCRtpCapabilities,
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionCapability,
    RTCRtpHeaderExtensionParameters,
    RTCRtpParameters,
)
from .rtcrtpreceiver import (
    RTCRtpContributingSource,
    RTCRtpReceiver,
    RTCRtpSynchronizationSource,
)
from .rtcrtpsender import RTCRtpSender
from .rtcrtptransceiver import RTCRtpTransceiver
from .rtcsctptransport import RTCSctpCapabilities, RTCSctpTransport
from .rtcsessiondescription import RTCSessionDescription
from .stats import (
    RTCInboundRtpStreamStats,
    RTCOutboundRtpStreamStats,
    RTCRemoteInboundRtpStreamStats,
    RTCRemoteOutboundRtpStreamStats,
    RTCStatsReport,
    RTCTransportStats,
)

__version__ = "1.13.0"

# Set default logging handler to avoid "No handler found" warnings.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "AudioStreamTrack",
    "InvalidAccessError",
    "InvalidStateError",
    "MediaStreamError",
    "MediaStreamTrack",
    "RTCBundlePolicy",
    "RTCCertificate",
    "RTCConfiguration",
    "RTCDataChannel",
    "RTCDataChannelParameters",
    "RTCDtlsFingerprint",
    "RTCDtlsParameters",
    "RTCDtlsTransport",
    "RTCIceCandidate",
    "RTCIceGatherer",
    "RTCIceParameters",
    "RTCIceServer",
    "RTCIceTransport",
    "RTCInboundRtpStreamStats",
    "RTCOutboundRtpStreamStats",
    "RTCPeerConnection",
    "RTCRemoteInboundRtpStreamStats",
    "RTCRemoteOutboundRtpStreamStats",
    "RTCRtcpParameters",
    "RTCRtpCapabilities",
    "RTCRtpCodecCapability",
    "RTCRtpCodecParameters",
    "RTCRtpContributingSource",
    "RTCRtpHeaderExtensionCapability",
    "RTCRtpHeaderExtensionParameters",
    "RTCRtpParameters",
    "RTCRtpReceiver",
    "RTCRtpSender",
    "RTCRtpSynchronizationSource",
    "RTCRtpTransceiver",
    "RTCSctpCapabilities",
    "RTCSctpTransport",
    "RTCSessionDescription",
    "RTCStatsReport",
    "RTCTransportStats",
    "VideoStreamTrack",
]
