API Reference
=============

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
          .. automethod:: wait_connected()
          .. autoattribute:: alpn_protocol
