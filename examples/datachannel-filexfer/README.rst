Data channel file transfer
==========================

This example illustrates sending a file over a data channel using an
RTCPeerConnection and a "copy and paste" signaling channel to exchange SDP.

First install the required packages:

.. code-block:: console

    $ pip install aiortc

On Linux and Mac OS X you can also install uvloop for better performance:

.. code-block:: console

    $ pip install uvloop

To run the example, you will need instances of the `filexfer` example:

- The first takes on the role of the offerer. It generates an offer which you
  must copy-and-paste to the answerer.

.. code-block:: console

   $ python filexfer.py send somefile.pdf

- The second takes on the role of the answerer. When given an offer, it will
  generate an answer which you must copy-and-paste to the offerer.

.. code-block:: console

   $ python filexfer.py receive received.pdf
