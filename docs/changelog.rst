Changelog
=========

.. currentmodule:: aiortc

0.9.11
------

Media
.....

  * Make AudioStreamTrack and VideoStreamTrack produce empty frames more regularly.

Examples
........

  * Fix a regession in copy-and-paste signaling which blocked the event loop.

0.9.10
------

Peer connection
...............

  * Send `raddr` and `rport` parameters for server reflexive and relayed candidates.
    This is required for Firefox to accept our STUN / TURN candidates.
  * Do not raise an exception if ICE or DTLS connection fails, just change state.

Media
.....

  * Revert to using asyncio's `run_in_executor` to send data to the encoder, it greatly
    reduces the response time.
  * Adjust package requirements to accept PyAV < 7.0.0.

Examples
........

  * `webcam` : force Chrome to use "unified-plan" semantics to enabled `addTransceiver`.
  * :class:`aiortc.contrib.media.MediaPlayer` : don't sleep at all when playing from webcam.
    This eliminates the constant one-second lag in the `webcam` demo.

0.9.9
-----

.. warning::

  `aiortc` now uses PyAV's :class:`~av.audio.frame.AudioFrame` and
  :class:`~av.video.frame.VideoFrame` classes instead of defining its own.

Media
.....

  * Use a jitter buffer for incoming audio.
  * Add :meth:`RTCPeerConnection.addTransceiver` method.
  * Add :attr:`RTCRtpTransceiver.direction` to manage transceiver direction.

Examples
........

  * `apprtc` : demonstrate the use of :class:`aiortc.contrib.media.MediaPlayer`
    and :class:`aiortc.contrib.media.MediaRecorder`.
  * `webcam` : new examples illustrating sending video from a webcam to a browser.
  * :class:`aiortc.contrib.media.MediaPlayer` : don't sleep if a frame lacks timing information.
  * :class:`aiortc.contrib.media.MediaPlayer` : remove `start()` and `stop()` methods.
  * :class:`aiortc.contrib.media.MediaRecorder` : use `libx264` for encoding.
  * :class:`aiortc.contrib.media.MediaRecorder` : make `start()` and `stop()` coroutines.

0.9.8
-----

Media
.....

  * Add support for H.264 video, a big thank you to @dsvictor94!
  * Add support for sending Receiver Estimate Maximum Bitrate (REMB) feedback.
  * Add support for parsing / serializing more RTP header extensions.
  * Move each media encoder / decoder its one thread instead of using a
    thread pool.

Statistics
..........

  * Add the :meth:`RTCPeerConnection.getStats()` coroutine to retrieve statistics.
  * Add initial :class:`RTCTransportStats` to report transport statistics.

Examples
........

  * Add new :class:`aiortc.contrib.media.MediaPlayer` class to read audio / video from a file.
  * Add new :class:`aiortc.contrib.media.MediaRecorder` class to write audio / video to a file.
  * Add new :class:`aiortc.contrib.media.MediaBlackhole` class to discard audio / video.

0.9.7
-----

Media
.....

  * Make RemoteStreamTrack emit an "ended" event, to simplify shutting down
    media consumers.
  * Add RemoteStreamTrack.readyState property.
  * Handle timestamp wraparound on sent RTP packets.

Packaging
.........

  * Add a versioned dependency on cffi>=1.0.0 to fix Raspberry Pi builds.

0.9.6
-----

Data channels
.............

  * Optimize reception for improved latency and throughput.

Media
.....

  * Add initial :meth:`RTCRtpReceiver.getStats()` and :meth:`RTCRtpReceiver.getStats()` coroutines.

Examples
........

  * `datachannel-cli`: display ping/pong roundtrip time.

0.9.5
-----

Media
.....

  * Make it possible to add multiple audio or video streams.

  * Implement basic RTP video packet loss detection / retransmission using RTCP NACK feedback.

  * Respond to Picture Loss Indications (PLI) by sending a keyframe.

  * Use shorter MID values to reduce RTP header extension overhead.

  * Correctly shutdown and discard unused transports when using BUNDLE.

Examples
........

  * `server` : make it possible to save received video to an AVI file.

0.9.4
-----

Peer connection
...............

  * Add support for TURN over TCP.

Examples
........

  * Add media and signaling helpers in `aiortc.contrib`.

  * Fix colorspace OpenCV colorspace conversions.

  * `apprtc` : send rotating image on video track.

0.9.3
-----

Media
.....

  * Set PictureID attribute on outgoing VP8 frames.

  * Negotiate and send SDES MID header extension for RTP packets.

  * Fix negative packets_lost encoding for RTCP reports.

0.9.2
-----

Data channels
.............

  * Numerous performance improvements in congestion control.

Examples
........

  * `datachannel-filexfer`: use uvloop instead of default asyncio loop.

0.9.1
-----

Data channels
.............

  * Revert making RTCDataChannel.send a coroutine.

0.9.0
-----

Media
.....

  * Enable post-processing in VP8 decoder to remove (macro) blocks.

  * Set target bitrate for VP8 encoder to 900kbps.

  * Re-create VP8 encoder if frame size changes.

  * Implement jitter estimation for RTCP reports.

  * Avoid overflowing the DLSR field for RTCP reports.

  * Raise video jitter buffer size.

Data channels
.............

  * BREAKING CHANGE: make RTCDataChannel.send a coroutine.

  * Support spec-compliant SDP format for datachannels, as used in Firefox 63.

  * Never send a negative advertised_cwnd.

Examples
........

  * `datachannel-filexfer`: new example for file transfer over a data channel.

  * `datachannel-vpn`: new example for a VPN over a data channel.

  * `server`: make it possible to select video resolution.

0.8.0
-----

Media
.....

  * Align VP8 settings with those used by WebRTC project, which greatly improves
    video quality.

  * Send RTCP source description, sender report, receiver report and bye packets.

Examples
........

  * `server`:

    - make it possible to not transform video at all.

    - allow video display to be up to 1280px wide.

  * `videostream-cli`:

    - fix Python 3.5 compatibility

Miscellaneous
.............

  * Delay logging string interpolation to reduce cost of packet logging in
    non-verbose mode.

0.7.0
-----

Peer connection
...............

  * Add :meth:`RTCPeerConnection.addIceCandidate()` method to handle trickled ICE candidates.

Media
.....

  * Make stop() methods of :class:`aiortc.RTCRtpReceiver`, :class:`aiortc.RTCRtpSender`
    and :class:`RTCRtpTransceiver` coroutines to enable clean shutdown.

Data channels
.............

  * Clean up :class:`aiortc.RTCDataChannel` shutdown sequence.

  * Support receiving an SCTP `RE-CONFIG` to raise number of inbound streams.

Examples
........

  * `server`:

    - perform some image processing using OpenCV.

    - make it possible to disable data channels.

    - make demo web interface more mobile-friendly.

  * `apprtc`:

    - automatically create a room if no room is specified on command line.

    - handle `bye` command.

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
