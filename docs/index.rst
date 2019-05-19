aioquic
=======

|travis| |codecov|

.. |travis| image:: https://img.shields.io/travis/com/aiortc/aioquic.svg
    :target: https://travis-ci.com/aiortc/aioquic

.. |codecov| image:: https://img.shields.io/codecov/c/github/aiortc/aioquic.svg
    :target: https://codecov.io/gh/aiortc/aioquic

``aioquic`` is a library for the QUIC network protocol in Python. It is built
on top of :mod:`asyncio`, Python's standard asynchronous I/O framework.

Here is a client which performs an HTTP/0.9 request:

.. literalinclude:: http_client.py

.. toctree::
   :maxdepth: 2

   api
   license
