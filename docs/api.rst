API Reference
=============

.. automodule:: aiortc

WebRTC
------

   .. autoclass:: RTCPeerConnection
      :members:

   .. autoclass:: RTCSessionDescription
      :members:

   .. autoclass:: RTCConfiguration
      :members:

Interactive Connectivity Establishment (ICE)
--------------------------------------------

   .. autoclass:: RTCIceCandidate
      :members:

   .. autoclass:: RTCIceGatherer
      :members:

   .. autoclass:: RTCIceTransport
      :members:

   .. autoclass:: RTCIceParameters
      :members:

   .. autoclass:: RTCIceServer
      :members:

Datagram Transport Layer Security (DTLS)
----------------------------------------

   .. autoclass:: RTCCertificate()
      :members:

   .. autoclass:: RTCDtlsTransport
      :members:

   .. autoclass:: RTCDtlsParameters()
      :members:

   .. autoclass:: RTCDtlsFingerprint()
      :members:

Real-time Transport Protocol (RTP)
----------------------------------

   .. autoclass:: RTCRtpReceiver
      :members:

   .. autoclass:: RTCRtpSender
      :members:

   .. autoclass:: RTCRtpTransceiver
      :members:

   .. autoclass:: RTCRtpSynchronizationSource()
      :members:

   .. autoclass:: RTCRtpCapabilities()
      :members:

   .. autoclass:: RTCRtpCodecCapability()
      :members:

   .. autoclass:: RTCRtpHeaderExtensionCapability()
      :members:

   .. autoclass:: RTCRtpParameters()
      :members:

   .. autoclass:: RTCRtpCodecParameters()
      :members:

   .. autoclass:: RTCRtcpParameters()
      :members:

Stream Control Transmission Protocol (SCTP)
-------------------------------------------

   .. autoclass:: RTCSctpTransport
      :members:

   .. autoclass:: RTCSctpCapabilities
      :members:

Data channels
-------------

   .. autoclass:: RTCDataChannel(transport, parameters)
      :members:

   .. autoclass:: RTCDataChannelParameters()
      :members:

Media
-----

   .. autoclass:: MediaStreamTrack
      :members:

Statistics
----------

   .. autoclass:: RTCStatsReport()

   .. autoclass:: RTCInboundRtpStreamStats()
      :members:

   .. autoclass:: RTCOutboundRtpStreamStats()
      :members:

   .. autoclass:: RTCRemoteInboundRtpStreamStats()
      :members:

   .. autoclass:: RTCRemoteOutboundRtpStreamStats()
      :members:

   .. autoclass:: RTCTransportStats()
      :members:
