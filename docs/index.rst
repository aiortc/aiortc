aioquic
=======

|travis| |codecov|

.. |travis| image:: https://img.shields.io/travis/com/aiortc/aioquic.svg
    :target: https://travis-ci.com/aiortc/aioquic

.. |codecov| image:: https://img.shields.io/codecov/c/github/aiortc/aioquic.svg
    :target: https://codecov.io/gh/aiortc/aioquic

``aioquic`` is a library for the QUIC network protocol in Python. It features several
APIs:

- a QUIC API following the "bring your own I/O" pattern, suitable for
  embedding in any framework,

- an HTTP/3 API which also follows the "bring your own I/O" pattern,

- a QUIC convenience API built on top of :mod:`asyncio`, Python's standard asynchronous
  I/O framework.

.. toctree::
   :maxdepth: 2

   quic
   http3
   asyncio
   license
