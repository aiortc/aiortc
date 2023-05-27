Webcam server
=============

This example illustrates how to read frames from a webcam and send them
to a browser.

Running
-------

First install the required packages:

.. code-block:: console

    $ pip install aiohttp aiortc

When you start the example, it will create an HTTP server which you
can connect to from your browser:

.. code-block:: console

    $ python webcam.py

You can then browse to the following page with your browser:

http://127.0.0.1:8080

Once you click `Start` the server will send video from its webcam to the
browser.

.. warning:: Due to the timing of when Firefox starts responding to mDNS
 requests and the current lack of ICE trickle support in aiortc, this example
 may not work with Firefox. For details see:

 https://github.com/aiortc/aiortc/issues/481 and
 https://bugzilla.mozilla.org/show_bug.cgi?id=1691189

Additional options
------------------

If you want to play a media file instead of using the webcam, run:

.. code-block:: console

   $ python webcam.py --play-from video.mp4

Pre-encoded Opus audio
......................

If you want to play an OGG file containing Opus audio without decoding the frames, run:

.. code-block:: console

   $ python webcam.py --play-from audio.ogg --play-without-decoding --audio-codec audio/opus

You can generate an example of such a file using:

.. code-block:: console

   $ ffmpeg -f lavfi -i "sine=frequency=1000:duration=20" -codec:a libopus -f ogg audio.ogg

Pre-encoded H.264 video
.......................

If you want to play an MPEGTS file containing H.264 video without decoding the frames, run:

.. code-block:: console

   $ python webcam.py --play-from video.ts --play-without-decoding --video-codec video/H264

You can generate an example of such a file using:

.. code-block:: console

   $ ffmpeg -f lavfi -i testsrc=duration=20:size=640x480:rate=30 -pix_fmt yuv420p -codec:v libx264 -profile:v baseline -level 31 -f mpegts video.ts

Pre-encoded VP8 video
.....................

If you want to play a WebM file containing VP8 video without decoding the frames, run:

.. code-block:: console

   $ python webcam.py --play-from video.webm --play-without-decoding --video-codec video/VP8

You can generate an example of such a file using:

.. code-block:: console

   $ ffmpeg -f lavfi -i testsrc=duration=20:size=640x480:rate=30 -codec:v vp8 -f webm video.webm

Credits
-------

The original idea for the example was from Marios Balamatsias.

Support for playback without decoding was based on an example by Renan Prata.
