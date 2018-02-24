aiowebrtc
=========

|travis| |coveralls|

.. |travis| image:: https://img.shields.io/travis/jlaine/aiowebrtc.svg
    :target: https://travis-ci.org/jlaine/aiowebrtc

.. |coveralls| image:: https://img.shields.io/coveralls/jlaine/aiowebrtc.svg
    :target: https://coveralls.io/github/jlaine/aiowebrtc

Asyncio-based WebRTC implementation.

This is a work in progress, but it is already possible to set up a connection
with an actual browser (tested with Chrome and Firefox).

Working:

- Basic SDP generation / parsing
- Interactive Connectivity Establishment
- DTLS handshake and SRTP keying
- SRTP encryption / decryption for RTP and RTCP

TODO:

- Actual media codec negotiation
- Expose media to API user
- Data channels
