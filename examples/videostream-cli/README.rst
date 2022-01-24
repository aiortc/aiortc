Video channel CLI
=================

This example illustrates the establishment of a video stream using an
RTCPeerConnection.

By default the signaling channel used is "copy and paste", but a number of
other signaling mecanisms are available.

By default the sent video is an animated French flag, but it is also possible
to use a MediaPlayer to read media from a file.

This example also illustrates how to use a MediaRecorder to capture media to a
file.

First install the required packages:

.. code-block:: console

   $ pip install aiortc opencv-python

Running the example
-------------------

To run the example, you will need instances of the `cli` example:

- The first takes on the role of the offerer. It generates an offer which you
  must copy-and-paste to the answerer.

.. code-block:: console

   $ python cli.py offer

- The second takes on the role of the answerer. When given an offer, it will
  generate an answer which you must copy-and-paste to the offerer.

.. code-block:: console

   $ python cli.py answer

Additional options
------------------

If you want to play a media file instead of sending the example image, run:

.. code-block:: console

   $ python cli.py --play-from video.mp4

If you want to recording the received video you can run one of the following:

.. code-block:: console

   $ python cli.py answer --record-to video.mp4
   $ python cli.py answer --record-to video-%3d.png
