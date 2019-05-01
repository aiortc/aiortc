Data channel file transfer
==========================

This example illustrates sending a file over a data channel using an
RTCPeerConnection and a "copy and paste" signaling channel to exchange SDP.

Currently, this "copy and paste" can't work on Windows platform due to asyncio functionality restriction.

So, if you want to run file transfer example on windows platform. please try `websocket signaling`_ or `p2p tcp-socket signaling`_.

..  _websocket signaling: https://github.com/aiortc/aiortc/blob/master/examples/datachannel-filexfer/README_WS_SIGNALING_VERSION.rst

..  _p2p tcp-socket signaling: https://github.com/aiortc/aiortc/pull/172/files#r279630394

.. 

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
