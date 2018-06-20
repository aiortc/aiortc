Changelog
=========

0.6.0
-----

Peer connection
...............

  * Make it possible to specify one STUN server and / or one TURN server.

  * Add `BUNDLE` support to use a single ICE/DTLS transport for multiple media.

  * Move media encoding / decoding off the main thread.

Data channels
.............

  * Use SCTP `ABORT` instead of `SHUTDOWN` when stopping :class:`aiortc.RTCSctpTransport`.

  * Advertise support for SCTP `RE-CONFIG` extension.

  * Make :class:`aiortc.RTCDataChannel` emit `open` and `close` events.

Examples
........

  * Add an example of how to connect to appr.tc.

  * Capture audio frames to a WAV file in server example.

  * Show datachannel open / close events in server example.
