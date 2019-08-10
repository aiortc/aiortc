aioquic
=======

|rtd| |pypi-v| |pypi-pyversions| |pypi-l| |travis| |codecov| |black|

.. |rtd| image:: https://readthedocs.org/projects/aioquic/badge/?version=latest
    :target: https://aioquic.readthedocs.io/

.. |pypi-v| image:: https://img.shields.io/pypi/v/aioquic.svg
    :target: https://pypi.python.org/pypi/aioquic

.. |pypi-pyversions| image:: https://img.shields.io/pypi/pyversions/aioquic.svg
    :target: https://pypi.python.org/pypi/aioquic

.. |pypi-l| image:: https://img.shields.io/pypi/l/aioquic.svg
    :target: https://pypi.python.org/pypi/aioquic

.. |travis| image:: https://img.shields.io/travis/com/aiortc/aioquic.svg
    :target: https://travis-ci.com/aiortc/aioquic

.. |codecov| image:: https://img.shields.io/codecov/c/github/aiortc/aioquic.svg
    :target: https://codecov.io/gh/aiortc/aioquic

.. |black| image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :target: https://github.com/python/black

What is ``aioquic``?
--------------------

``aioquic`` is a library for the QUIC network protocol in Python. It features
a minimal TLS 1.3 implementation, a QUIC stack and an HTTP/3 stack.

QUIC standardisation is not finalised yet, but ``aioquic`` closely tracks the
specification drafts and is regularly tested for interoperability against other
`QUIC implementations`_.

To learn more about ``aioquic`` please `read the documentation`_.

Design and features
-------------------

TLS 1.3
.......

``aioquic`` features a minimal TLS 1.3 implementation built upon the
`cryptography`_ library. This is because QUIC requires some APIs which are
currently unavailable in mainstream TLS implementations such as OpenSSL:

- the ability to extract traffic secrets

- the ability to operate directly on TLS messages, without using the TLS
  record layer

Sans-IO APIs
............

Both the QUIC and the HTTP/3 APIs follow the "bring your own I/O" pattern,
leaving actual I/O operations to the API user. This approach has a number of
advantages including making the code testable and allowing integration with
different concurrency models.

Running the examples
--------------------

``aioquic`` requires Python 3.6 or better. After checking out the code using
git you can run:

.. code-block:: console

   $ pip install -e .
   $ pip install aiofiles starlette

You can now run the example server, which handles both HTTP/0.9 and HTTP/3:

.. code-block:: console

   $ python examples/http3-server.py --certificate tests/ssl_cert.pem --private-key tests/ssl_key.pem

You can also run the example client to perform an HTTP/3 request:

.. code-block:: console

  $ python examples/http3-client.py https://localhost:4433/

Alternatively you can perform an HTTP/0.9 request:

.. code-block:: console

  $ python examples/http3-client.py --legacy-http https://localhost:4433/


License
-------

``aioquic`` is released under the `BSD license`_.

.. _read the documentation: https://aioquic.readthedocs.io/en/latest/
.. _QUIC implementations: https://github.com/quicwg/base-drafts/wiki/Implementations
.. _cryptography: https://cryptography.io/
.. _BSD license: https://aioquic.readthedocs.io/en/latest/license.html
