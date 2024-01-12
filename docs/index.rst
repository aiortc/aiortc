aiortc
=========

.. image:: https://img.shields.io/pypi/l/aiortc.svg
   :target: https://pypi.python.org/pypi/aiortc
   :alt: License

.. image:: https://img.shields.io/pypi/v/aiortc.svg
   :target: https://pypi.python.org/pypi/aiortc
   :alt: Version

.. image:: https://img.shields.io/pypi/pyversions/aiortc.svg
   :target: https://pypi.python.org/pypi/aiortc
   :alt: Python versions

.. image:: https://github.com/aiortc/aiortc/workflows/tests/badge.svg
   :target: https://github.com/aiortc/aiortc/actions
   :alt: Tests

.. image:: https://img.shields.io/codecov/c/github/aiortc/aiortc.svg
   :target: https://codecov.io/gh/aiortc/aiortc
   :alt: Coverage

``aiortc`` is a library for `Web Real-Time Communication (WebRTC)`_ and
`Object Real-Time Communication (ORTC)`_ in Python. It is built on top of
``asyncio``, Python's standard asynchronous I/O framework.

The API closely follows its Javascript counterpart while using pythonic
constructs:

- promises are replaced by coroutines
- events are emitted using ``pyee.EventEmitter``

.. _Web Real-Time Communication (WebRTC): https://webrtc.org/
.. _Object Real-Time Communication (ORTC): https://ortc.org/

Why should I use ``aiortc``?
----------------------------

The main WebRTC and ORTC implementations are either built into web browsers,
or come in the form of native code. While they are extensively battle tested,
their internals are complex and they do not provide Python bindings.
Furthermore they are tightly coupled to a media stack, making it hard to plug
in audio or video processing algorithms.

In contrast, the ``aiortc`` implementation is fairly simple and readable. As
such it is a good starting point for programmers wishing to understand how
WebRTC works or tinker with its internals. It is also easy to create innovative
products by leveraging the extensive modules available in the Python ecosystem.
For instance you can build a full server handling both signaling and data
channels or apply computer vision algorithms to video frames using OpenCV.

Furthermore, a lot of effort has gone into writing an extensive test suite for
the ``aiortc`` code to ensure best-in-class code quality.

.. toctree::
   :maxdepth: 2

   examples
   api
   helpers
   contributing
   changelog
   license
