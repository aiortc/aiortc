Low-level API
=============

The low-level API performs no I/O on its own, leaving this to the API user.
This allows you to integrate QUIC in any Python application, regardless of
the concurrency model you are using.

Connection
----------

.. automodule:: aioquic.connection

    .. autoclass:: QuicConnection
        :members:

Events
------

.. automodule:: aioquic.events

    .. autoclass:: ConnectionTerminated
        :members:

    .. autoclass:: HandshakeCompleted
        :members:

    .. autoclass:: PingAcknowledged
        :members:

    .. autoclass:: StreamDataReceived
        :members:

    .. autoclass:: StreamReset
        :members:
