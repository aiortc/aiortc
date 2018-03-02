aiortc
======

|rtd| |pypi-l| |travis| |coveralls|

.. |rtd| image:: https://readthedocs.org/projects/aiortc/badge/?version=latest
   :target: https://aiortc.readthedocs.io/

.. |pypi-l| image:: https://img.shields.io/pypi/l/aiortc.svg
    :target: https://pypi.python.org/pypi/aiortc

.. |travis| image:: https://img.shields.io/travis/jlaine/aiortc.svg
    :target: https://travis-ci.org/jlaine/aiortc

.. |coveralls| image:: https://img.shields.io/coveralls/jlaine/aiortc.svg
    :target: https://coveralls.io/github/jlaine/aiortc

What is ``aiortc``?
-------------------

``aiortc`` is a library for WebRTC (Web Real-Time Communication) in Python. It
is built on top of ``asyncio``, Python's standard asynchronous I/O framework.

The API closely follows its Javascript counterpart while using pythonic
constructs:

- promises are replaced by coroutines
- events are emitted using ``pyee.EventEmitter``

To learn more about ``aiortc`` please `read the documentation`_.

.. _read the documentation: https://aiortc.readthedocs.io/en/latest/

Implementation status
---------------------

``aiortc`` is a work in progress, but it is already possible to set up a
connection with an actual browser (tested with Chrome and Firefox), exchange
messages over a data channel and send audio to the browser.

Working:

- Basic SDP generation / parsing
- Interactive Connectivity Establishment
- DTLS handshake, encryption / decryption (for SCTP)
- SRTP keying, encryption and decryption for RTP and RTCP
- Minimal SCTP implementation
- Data Channels

TODO:

- Actual media codec negotiation
- Expose media reception API
- ICE trickle
- Video streams

Requirements
------------

Currently, you need a development version of ``cryptography`` to use ``aiortc``,
but this will no longer be the case once ``cryptography`` 2.2 is released.

On Debian/Ubuntu run:

    apt install libopus-dev libsrtp2-dev libssl-dev
    pip install -e git://github.com/pyca/cryptography.git@a36579b6e4086ded4c20578bbfbfae083d5e6bce#egg=cryptography

On OS X run:

    brew install opus srtp openssl@1.1
    export LDFLAGS="-L$(brew --prefix openssl@1.1)/lib"
    export CFLAGS="-I$(brew --prefix openssl@1.1)/include"
    pip install -e git://github.com/pyca/cryptography.git@a36579b6e4086ded4c20578bbfbfae083d5e6bce#egg=cryptography

License
-------

``aiortc`` is released under the `BSD license`_.

.. _BSD license: https://aiortc.readthedocs.io/en/latest/license.html
