1. Janus video room client example
==================================

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

2. janus_v4l2 video room client example
=======================================

To demonstrate connecting to a Janus videoroom and writing the first
participant video stream to a local v4l2loopback[1] dummy device (e.g. /dev/video2),
where it can be tested by doing "ffplay -i /dev/video2" from another window.


If you want to join a room, stream a video file to it and redirect the first user's
stream to a local v4l2 device, run the command below.

The dummy device (e.g. /dev/video2) must first be created when installing the
driver. For example:

.. code-block:: console

  $ sudo modprobe v4l2loopback devices=2

Supply your own demo.mp4 video file that will show as a participant in the room.

.. code-block:: console

  $ python3 janus3.py --room 1234 --play-from demo.mp4 --record-to /dev/video2 http://localhost:8088/janus


[1] https://github.com/umlaeute/v4l2loopback
