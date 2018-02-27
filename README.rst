aiortc
======

|rtd| |travis| |coveralls|

.. |rtd| image:: https://readthedocs.org/projects/aiortc/badge/?version=latest
   :target: https://aiortc.readthedocs.io/

.. |travis| image:: https://img.shields.io/travis/jlaine/aiortc.svg
    :target: https://travis-ci.org/jlaine/aiortc

.. |coveralls| image:: https://img.shields.io/coveralls/jlaine/aiortc.svg
    :target: https://coveralls.io/github/jlaine/aiortc

Asyncio-based WebRTC implementation.

This is a work in progress, but it is already possible to set up a connection
with an actual browser (tested with Chrome and Firefox).

Working:

- Basic SDP generation / parsing
- Interactive Connectivity Establishment
- DTLS handshake, SRTP keying and encryption
- SRTP encryption / decryption for RTP and RTCP
- Data channels

TODO:

- Actual media codec negotiation
- Expose media to API user

Note
----

Currently, you need a development version of ``cryptography`` to use ``aiortc``,
but this will no longer be the case once ``cryptography`` 2.2 is released.

License
-------

``aiortc`` is released under the `BSD license`_.

.. _BSD license: https://aiortc.readthedocs.io/en/latest/license.html
