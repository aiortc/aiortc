Janus video room client
=======================

This example illustrates how to connect to the Janus WebRTC server's video room.

By default it simply sends green video frames, but you can instead specify a
video file to stream to the room.

First install the required packages:

.. code-block:: console

    $ pip install aiohttp aiortc

When you run the example, it will connect to Janus and join the '1234' room:

.. code-block:: console

   $ python janus.py http://localhost:8088/janus

Additional options
------------------

If you want to join a different room, run:

.. code-block:: console

   $ python janus.py --room 5678 http://localhost:8088/janus

If you want to play a media file instead of sending green video frames, run:

.. code-block:: console

   $ python janus.py --play-from video.mp4 http://localhost:8088/janus

If you want to play an MPEGTS file containing H.264 video without decoding the frames, run:

.. code-block:: console

   $ python janus.py --play-from <video.ts> --play-without-decoding

You can generate an example of such a file using:

.. code-block:: console

   $ ffmpeg -f lavfi -i testsrc=duration=20:size=640x480:rate=30 -pix_fmt yuv420p -codec:v libx264 -profile:v baseline -level 31 -f mpegts video.ts

In this case, janus video room must be configured to only allow a single video codec, the one you use.
