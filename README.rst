aioquic
=======

|rtd| |travis| |codecov| |black|

.. |rtd| image:: https://readthedocs.org/projects/aioquic/badge/?version=latest
    :target: https://aioquic.readthedocs.io/

.. |travis| image:: https://img.shields.io/travis/com/aiortc/aioquic.svg
    :target: https://travis-ci.com/aiortc/aioquic

.. |codecov| image:: https://img.shields.io/codecov/c/github/aiortc/aioquic.svg
    :target: https://codecov.io/gh/aiortc/aioquic

.. |black| image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :target: https://github.com/python/black

What is ``aioquic``?
--------------------

``aioquic`` is a library for the QUIC network protocol in Python. It is built
on top of ``asyncio``, Python's standard asynchronous I/O framework.

``aioquic`` features a minimal TLS 1.3 implementation built upon the
`cryptography`_ library. This is because QUIC requires some APIs which are
currently unavailable in mainstream TLS implementations such as OpenSSL:

- the ability to extract traffic secrets

- the ability to operate directly on TLS messages, without using the TLS
  record layer

Status
------

``aioquic`` is still a work in progress, and the API is not finalized.

License
-------

``aioquic`` is released under the `BSD license`_.

.. _cryptography: https://cryptography.io/
.. _BSD license: https://aioquic.readthedocs.io/en/latest/license.html
