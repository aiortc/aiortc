Webcam server
=============

This example illustrates how to use a webcam built-in H264 encoding capabilities to send h264 encoded video to the browser.

Running
-------

Adjust in the code the dev entry for your camera

First install the required packages:

.. code-block:: console

    $ pip install aiohttp aiortc opencv-python

When you start the example, it will create an HTTP server which you
can connect to from your browser:

.. code-block:: console

    $ python gstreamercam.py

You can then browse to the following page with your browser:

http://127.0.0.1:8080

Once you click `Start` the server will send video from the raspberry pi camera to the
browser.

