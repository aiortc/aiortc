Webcam server
=============

This example illustrates how to read frames from a webcam and send them
to a browser.

Running
-------

First install the required packages:

.. code-block:: console

    $ pip install aiohttp aiortc opencv-python

When you start the example, it will create an HTTP server which you
can connect to from your browser:

.. code-block:: console

    $ python webcam.py

You can then browse to the following page with your browser:

http://127.0.0.1:8080

Once you click `Start` the server will send video from its webcam to the
browser.

Additional options
------------------

If you want to play a media file instead of using the webcam, run:

.. code-block:: console

   $ python webcam.py --play-from video.mp4

Credits
-------

The original idea for the example was from Marios Balamatsias.
