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

``aiortc`` is a library for `Web Real-Time Communication (WebRTC)`_ and
`Object Real-Time Communication (ORTC)`_ in Python. It is built on top of
``asyncio``, Python's standard asynchronous I/O framework.

The API closely follows its Javascript counterpart while using pythonic
constructs:

- promises are replaced by coroutines
- events are emitted using ``pyee.EventEmitter``

To learn more about ``aiortc`` please `read the documentation`_.

.. _Web Real-Time Communication (WebRTC): https://webrtc.org/
.. _Object Real-Time Communication (ORTC): https://ortc.org/
.. _read the documentation: https://aiortc.readthedocs.io/en/latest/

Why should I use ``aiortc``?
----------------------------

The main WebRTC and ORTC implementations are either built into web browsers,
or come in the form of native code. While they are extensively battle tested,
their internals are complex and they do not provide Python bindings.
Furthermore they are tightly coupled to a media stack, making it hard to plug
in audio or video processing algorithms.

In contrast, the ``aiortc`` implementation is fairly simple and readable. As
such it is a good starting point for programmers wishing to understand how
WebRTC works or tinker with its internals. It is also easy to create innovative
products by leveraging the extensive modules available in the Python ecosystem.
For instance you can build a full server handling both signaling and data
channels or apply computer vision algorithms to video frames using OpenCV.

Implementation status
---------------------

``aiortc`` is a work in progress, but it is already possible to set up a
connection with an actual browser (tested with Chrome and Firefox), exchange
messages over a data channel and audio / video with the browser.

Working:

- SDP generation / parsing
- Interactive Connectivity Establishment
- DTLS key and certificate generation
- DTLS handshake, encryption / decryption (for SCTP)
- SRTP keying, encryption and decryption for RTP and RTCP
- Simple SCTP implementation
- Data Channels
- Sending and receiving audio (Opus / PCMU / PCMA)
- Sending and receiving video (VP8)

TODO:

- SCTP retransmission and receiver window handling
- ICE trickle

Requirements
------------

In addition to aiortc's Python dependencies you need a couple of libraries
installed on your system for media codecs and encryption.

On Debian/Ubuntu run:

.. code:: bash

    apt install libopus-dev libsrtp2-dev libssl-dev libvpx-dev

On OS X run:

.. code:: bash

    brew install opus srtp openssl@1.1 libvpx
    export LDFLAGS="-L$(brew --prefix openssl@1.1)/lib"
    export CFLAGS="-I$(brew --prefix openssl@1.1)/include"

License
-------

``aiortc`` is released under the `BSD license`_.

.. _BSD license: https://aiortc.readthedocs.io/en/latest/license.html
