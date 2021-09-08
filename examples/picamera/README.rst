Webcam server
=============

This example illustrates how to use a raspberry pi and picamera to send h264 encoded video (using hardware encoder) to the browser.

Running
-------

First install the required packages:

.. code-block:: console

    $ pip install aiohttp aiortc picamera

When you start the example, it will create an HTTP server which you
can connect to from your browser:

.. code-block:: console

    $ python picam.py

You can then browse to the following page with your browser:

http://127.0.0.1:8080

Once you click `Start` the server will send video from the raspberry pi camera to the
browser.

