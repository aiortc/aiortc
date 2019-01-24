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
