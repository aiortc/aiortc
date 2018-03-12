API Reference
=============

.. automodule:: aiortc

WebRTC
------

   .. autoclass:: RTCPeerConnection
      :members:

   .. autoclass:: RTCSessionDescription
      :members:

Interactive Connectivity Establishment (ICE)
--------------------------------------------

   .. autoclass:: RTCIceGatherer
      :members:

   .. autoclass:: RTCIceTransport
      :members:

   .. autoclass:: RTCIceParameters
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
