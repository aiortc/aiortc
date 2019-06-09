API Reference
=============

.. automodule:: aioquic

Client
------

     .. autofunction:: connect

Server
------

     .. autofunction:: serve

Common
------

     .. autoclass:: QuicConnection

          .. automethod:: close()
          .. automethod:: create_stream()
          .. automethod:: ping()
          .. automethod:: wait_connected()
          .. autoattribute:: alpn_protocol
