# ruff: noqa: F401
import logging

import av.logging

from .exceptions import InvalidAccessError, InvalidStateError
from .mediastreams import (
    AudioStreamTrack,
    MediaStreamError,
    MediaStreamTrack,
    VideoStreamTrack,
)
from .rtcconfiguration import RTCConfiguration, RTCIceServer
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

__version__ = "1.11.0"

# Disable PyAV's logging framework as it can lead to thread deadlocks.
av.logging.restore_default_callback()

# Set default logging handler to avoid "No handler found" warnings.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "AudioStreamTrack",
    "InvalidAccessError",
    "InvalidStateError",
    "MediaStreamError",
    "MediaStreamTrack",
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
