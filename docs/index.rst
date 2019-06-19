aioquic
=======

|travis| |codecov|

.. |travis| image:: https://img.shields.io/travis/com/aiortc/aioquic.svg
    :target: https://travis-ci.com/aiortc/aioquic

.. |codecov| image:: https://img.shields.io/codecov/c/github/aiortc/aioquic.svg
    :target: https://codecov.io/gh/aiortc/aioquic

``aioquic`` is a library for the QUIC network protocol in Python. It features two
APIs:

- a high-level API built on top of :mod:`asyncio`, Python's standard asynchronous
  I/O framework.

- a low-level "bring your own I/O" API suitable for embedding in any framework

Here is a client which performs an HTTP/0.9 request using asyncio:

.. literalinclude:: http_client.py

.. toctree::
   :maxdepth: 2

   asyncio
   sansio
   license
