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
          .. autoattribute:: alpn_protocol
