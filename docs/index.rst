aiortc
=========

|travis| |coveralls|

.. |travis| image:: https://img.shields.io/travis/jlaine/aiortc.svg
    :target: https://travis-ci.org/jlaine/aiortc

.. |coveralls| image:: https://img.shields.io/coveralls/jlaine/aiortc.svg
    :target: https://coveralls.io/github/jlaine/aiortc

``aiortc`` is a library for WebRTC (Web Real-Time Communication) in Python. It
is built on top of ``asyncio``, Python's standard asynchronous I/O framework.

The API closely follows its Javascript counterpart while using pythonic
constructs:

- promises are replaced by coroutines
- events are emitted using ``pyee.EventEmitter``

.. toctree::
   :maxdepth: 2

   api
   license
