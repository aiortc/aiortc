Audio, video and data channel server
====================================

This example illustrates establishing audio, video and a data channel with a
browser.

Running
-------

To run this example, you will need to install ``aiohttp``:

.. code-block:: console

    $ pip install aiohttp

When you start the example, it will create an HTTP server which you
can connect to from your browser:

.. code-block:: console

    $ python server.py

Running via Docker
``````````````````

* Build docker container: `docker-compose build`
* Run docker container: `docker-compose up`

You can then browse to the following page with your browser:

http://127.0.0.1:8080

Once you click `Start` the browser will send the audio and video from its
webcam to the server.

The server will play a pre-recorded audio clip and alternately send a green
square and the received video back to the browser.

In parallel to media streams, the browser sends a 'ping' message over the data
channel, and the server replies with 'pong'.

Credits
-------

The audio file "demo-instruct.wav" was borrowed from the Asterisk
project. It is licensed as Creative Commons Attribution-Share Alike 3.0:

https://wiki.asterisk.org/wiki/display/AST/Voice+Prompts+and+Music+on+Hold+License
