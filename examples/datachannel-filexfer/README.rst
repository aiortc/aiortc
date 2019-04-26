Data channel file transfer
==========================

- ``Important``:

  - Copy and paste signaling example can not work on Windows python environment due to restricton of asyncio.

  - To try file transfer example on windows environment, please see `websocket signaling version`_.

This example illustrates sending a file over a data channel using an
RTCPeerConnection and a "copy and paste" signaling channel to exchange SDP.

..  _websocket signaling version: https://github.com/aiortc/aiortc/blob/master/examples/datachannel-filexfer/README_WS_SIGNALING_VERSION.rst


First install the required packages:

.. code-block:: console

    $ pip install aiortc uvloop


To run the example, you will need instances of the `filexfer` example:

- The first takes on the role of the offerer. It generates an offer which you
  must copy-and-paste to the answerer.

.. code-block:: console

   $ python filexfer.py send somefile.pdf

- The second takes on the role of the answerer. When given an offer, it will
  generate an answer which you must copy-and-paste to the offerer.

.. code-block:: console

   $ python filexfer.py receive received.pdf
