Changelog
=========

.. currentmodule:: aiortc

1.5.0
-----

 * Make H.264 send a full picture when picture loss occurs.
 * Fix TURN over TCP by updating `aioice` to 0.9.0.
 * Make use of the `ifaddr` package instead of the unmaintained `netifaces` package.

1.4.0
-----

 * Build wheels for Python 3.11.
 * Allow :class:`aiortc.contrib.media.MediaPlayer` to send media without transcoding.
 * Allow :class:`aiortc.contrib.media.MediaPlayer` to specify a timeout when opening media.
 * Make :class:`aiortc.RTCSctpTransport` transmit packets sooner to reduce datachannel latency.
 * Refactor :class:`aiortc.RTCDtlsTransport` to use PyOpenSSL.
 * Make :class:`aiortc.RTCPeerConnection` log sent and received SDP when using verbose logging.

1.3.2
-----

 * Limit size of NACK reports to avoid excessive packet size.
 * Improve H.264 codec matching.
 * Determine video size from first frame received by :class:`aiortc.contrib.media.MediaRecorder`.
 * Fix a deprecation warning when using `av` >= 9.1.0.
 * Tolerate STUN URLs containing a `protocol` querystring argument.

1.3.1
-----

 * Build wheels for aarch64 on Linux.
 * Adapt :class:`aiortc.contrib.media.MediaPlayer` for PyAV 9.x.
 * Ensure H.264 produces B-frames by resetting picture type.

1.3.0
-----

 * Build wheels for Python 3.10 and for arm64 on Mac.
 * Build wheels against `libvpx` 1.10.
 * Add support for looping in :class:`aiortc.contrib.media.MediaPlayer`.
 * Add unbuffered option to :class:`aiortc.contrib.media.MediaRelay`.
 * Calculate audio energy and send in RTP header extension.
 * Fix a race condition in RTP sender/receiver shutdown.
 * Improve performance of H.264 bitstream splitting code.
 * Update imports for `pyee` version 9.x.
 * Fully switch to `google-crc32c` instead of `crc32`.
 * Drop support for Python 3.6.
 * Remove `apprtc` code as the service is no longer publicly hosted.

1.2.1
-----

 * Add a clear error message when no common codec is found.
 * Replace the `crc32` dependency with `google-crc32c` which offers a more
   liberal license.

1.2.0
-----

 * Fix jitter buffer to avoid severe picture corruption under packet loss and
   send Picture Loss Indication (PLI) when needed.
 * Make H.264 encoder honour the bitrate from the bandwidth estimator.
 * Add support for hardware-accelerated H.264 encoding on Raspberry Pi 4 using
   the `h264_omx` codec.
 * Add :class:`aiortc.contrib.media.MediaRelay` class to allow sending media
   tracks to multiple consumers.

1.1.2
-----

 * Add :attr:`RTCPeerConnection.connectionState` property.
 * Correctly detect RTCIceTransport `"failed"` state.
 * Correctly route RTP packets when there are multiple tracks of the same kind.
 * Use full module name to name loggers.

1.1.1
-----

 * Defer adding remote candidates until after transport bundling to avoid
   unnecessary mDNS lookups.

1.1.0
-----

 * Add support for resolving mDNS candidates.
 * Improve support for TURN, especially long-lived connections.

1.0.0
-----

Breaking
........

 * Make :meth:`RTCPeerConnection.addIceCandidate` a coroutine.
 * Make :meth:`RTCIceTransport.addRemoteCandidate` a coroutine.

Media
.....

 * Handle SSRC attributes in SDP containing a colon (#372).
 * Limit number of H.264 NALU per packet (#394, #426).

Examples
........

 * `server` make it possible to specify bind address (#347).

0.9.28
------

Provide binary wheels for Linux, Mac and Windows on PyPI.

0.9.27
------

Data channels
.............

 * Add :attr:`RTCSctpTransport.maxChannels` property.
 * Recycle stream IDs (#256).
 * Correctly close data channel when SCTP is not established (#300).

Media
.....

 * Add add :attr:`RTCRtpReceiver.track` property (#298).
 * Fix a crash in `AimdRateControl` (#295).

0.9.26
------

DTLS
....

  * Drop support for OpenSSL < 1.0.2.

Examples
........

  * `apprtc` fix handling of empty "candidate" message.

Media
.....

  * Fix a MediaPlayer crash when stopping one track of a multi-track file (#237, #274).
  * Fix a MediaPlayer error when stopping a track while waiting for the next frame.
  * Make `RTCRtpSender` resilient to exceptions raised by media stream tracks (#283).

0.9.25
------

Media
.....

  * Do not repeatedly send key frames after receiving a PLI.

SDP
...

  * Do not try to determine track ID if there is no Msid.
  * Accept a star in rtcp-fb attributes.

0.9.24
------

Peer connection
...............

  * Assign DTLS role based on the SDP negotiation, not the resolved ICE role.
  * When the peer is ICE lite, adopt the ICE controlling role, and do not use
    agressive nomination.
  * Do not close transport on `setRemoteDescription` if media and data are
    bundled.
  * Set RemoteStreamTrack.id based on the Msid.

Media
.....

  * Support alsa hardware output in MediaRecorder.

SDP
...

  * Add support for the `ice-lite` attribute.
  * Add support for receiving session-level `ice-ufrag`, `ice-pwd` and `setup`
    attributes.

Miscellaneous
.............

  * Switch from `attrs` to standard Python `dataclasses`.
  * Use PEP-526 style variable annotations instead of comments.

0.9.23
------

  * Drop support for Python 3.5.
  * Drop dependency on PyOpenSSL.
  * Use PyAV >= 7.0.0.
  * Add partial type hints.

0.9.22
------

DTLS
....

  * Display exception if data handler fails.

Examples
........

  * `server` and `webcam` : add playsinline attribute for iOS compatibility.
  * `webcam` : make it possible to play media from a file.

Miscellaneous
.............

  * Use aioice >= 0.6.15 to not fail on mDNS candidates.
  * Use pyee version 6.x.

0.9.21
------

DTLS
....

  * Call SSL_CTX_set_ecdh_auto for OpenSSL 1.0.2.

Media
.....

  * Correctly route REMB packets to the :class:`aiortc.RTCRtpSender`.

Examples
........

  * :class:`aiortc.contrib.media.MediaPlayer` : release resources (e.g. webcam) when the player stops.
  * :class:`aiortc.contrib.signaling.ApprtcSignaling` : make AppRTC signaling available for more examples.
  * `datachannel-cli` : make uvloop optional.
  * `videostream-cli` : animate the flag with a wave effect.
  * `webcam` : explicitly set frame rate to 30 fps for webcams.

0.9.20
------

Data channels
.............

  * Support out-of-band negotiation and custom channel id.

Documentation
.............

  * Fix documentation build by installing `crc32c` instead of `crcmod`.

Examples
........

  * :class:`aiortc.contrib.media.MediaPlayer` : skip frames with no presentation timestamp (pts).

0.9.19
------

Data channels
.............

  * Do not raise congestion window when it is not fully utilized.
  * Fix Highest TSN Newly Acknowledged logic for striking lost chunks.
  * Do not limit congestion window to 120kB, limit burst size instead.

Media
.....

  * Skip RTX packets with an empty payload.

Examples
........

  * `apprtc` : make the initiator send messages using an HTTP POST instead of WebSocket.
  * `janus` : new example to connect to the Janus WebRTC server.
  * `server` : add cartoon effect to video transforms.

0.9.18
------

DTLS
....

  * Do not use DTLSv1_get_timeout after DTLS handshake completes.

Data channels
.............

  * Add setter for :attr:`RTCDataChannel.bufferedAmountLowThreshold`.
  * Use `crc32c` package instead of `crcmod`, it provides better performance.
  * Improve parsing and serialization code performance.
  * Disable logging code if it is not used to improve performance.

0.9.17
------

DTLS
....

  * Do not bomb if SRTP is received before DTLS handshake completes.

Data channels
.............

  * Implement unordered delivery, so that the `ordered` option is honoured.
  * Implement partial reliability, so that the `maxRetransmits` and `maxPacketLifeTime` options are honoured.

Media
.....

  * Put all tracks in the same stream for now, fixes breakage introduced in 0.9.14.
  * Use case-insensitive comparison for codec names.
  * Use a=msid attribute in SDP instead of SSRC-level attributes.

Examples
........

  * `server` : make it possible to select unreliable mode for data channels.
  * `server` : print the round-trip time for data channel messages.

0.9.16
------

DTLS
....

  * Log OpenSSL errors if the DTLS handshake fails.
  * Fix DTLS handshake in server mode with OpenSSL < 1.1.0.

Media
.....

  * Add :meth:`RTCRtpReceiver.getCapabilities` and :meth:`RTCRtpSender.getCapabilities`.
  * Add :meth:`RTCRtpReceiver.getSynchronizationSources`.
  * Add :meth:`RTCRtpTransceiver.setCodecPreferences`.

Examples
........

  * `server` : make it possible to force audio codec.
  * `server` : shutdown cleanly on Chrome which lacks :meth:`RTCRtpTransceiver.stop`.

0.9.15
------

Data channels
.............

  * Emit a warning if the crcmod C extension is not present.

Media
.....

  * Support subsequent offer / answer exchanges.
  * Route RTCP parameters to RTP receiver and sender independently.
  * Fix a regression when the remote SSRC are not known.
  * Fix VP8 descriptor parsing errors detected by fuzzing.
  * Fix H264 descriptor parsing errors detected by fuzzing.

0.9.14
------

Media
.....

  * Add support for RTX retransmission packets.
  * Fix RTP and RTCP parsing errors detected by fuzzing.
  * Use case-insensitive comparison for hash algorithm in SDP, fixes interoperability with Asterisk.
  * Offer NACK PLI and REMB feedback mechanisms for H.264.

0.9.13
------

Data channels
.............

  * Raise an exception if :meth:`RTCDataChannel.send` is called when readyState is not `'open'`.
  * Do not use stream sequence number for unordered data channels.

Media
.....

  * Set VP8 target bitrate according to Receiver Estimated Maximum Bandwidth.

Examples
........

  * Correctly handle encoding in copy-and-paste signaling.
  * `server` : add command line options to use HTTPS.
  * `webcam` : add command line options to use HTTPS.
  * `webcam` : add code to open webcam on OS X.

0.9.12
------

  * Rework code in order to facilitate garbage collection and avoid memory leaks.

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
