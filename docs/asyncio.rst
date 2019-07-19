asyncio API
===========

The asyncio API provides a high-level QUIC API built on top of :mod:`asyncio`,
Python's standard asynchronous I/O framework.

Here is a client which performs an HTTP/0.9 request using asyncio:

.. literalinclude:: http_client.py

.. automodule:: aioquic.asyncio

Client
------

    .. autofunction:: connect

Server
------

    .. autofunction:: serve

Common
------

    .. autoclass:: QuicConnectionProtocol

        .. automethod:: close()
        .. automethod:: create_stream()
        .. automethod:: ping()
        .. automethod:: wait_closed()
        .. automethod:: wait_connected()
