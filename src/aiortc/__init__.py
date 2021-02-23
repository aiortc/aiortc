# flake8: noqa
import logging

import av.logging

from .about import __version__
from .exceptions import InvalidAccessError, InvalidStateError
from .mediastreams import MediaStreamTrack, VideoStreamTrack
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

# Disable PyAV's logging framework as it can lead to thread deadlocks.
av.logging.restore_default_callback()

# Set default logging handler to avoid "No handler found" warnings.
logging.getLogger(__name__).addHandler(logging.NullHandler())
