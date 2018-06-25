Video channel CLI
=================

This example illustrates the establishment of a video stream using an
RTCPeerConnection and a "copy and paste" signaling channel to exchange SDP.

The video stream is composed of three separate streams that have been combined
into a single stream that is three times as wide.

First install the required packages:

.. code-block:: console

   $ pip install aiortc opencv-python

To run the example, you will need instances of the `cli` example:

- The first takes on the role of the offerer. It generates an offer which you
  must copy-and-paste to the answerer.

.. code-block:: console

   $ python cli.py offer

- The second takes on the role of the answerer. When given an offer, it will
  generate an answer which you must copy-and-paste to the offerer.

.. code-block:: console

   $ python cli.py answer

Check `output.png` for the result of the stream.
