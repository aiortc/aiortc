Remux server 
=============

This example illustrates how to remux a video stream that is originally not packed in MPEGTS container so that it can be streamed without transcoding. An example usecase is playing the video content from an RTSP source that sends raw H265 streams.


Running
-------

First install the required packages:

.. code-block:: console

    $ pip install aiohttp aiortc

When you start the example, it will create an HTTP server which you can connect to from your browser. You should pass video source address through the "--play-from" and specify the source encoding, for example:

.. code-block:: console

    $ python server.py --play-from=rtsp://localhost:8554/webcam --video-codec="video/H264"

You can then browse to the following page with your browser:

http://127.0.0.1:8080

Once you click `Start` the server will send video from its webcam to the
browser.

.. warning:: Due to the timing of when Firefox starts responding to mDNS
 requests and the current lack of ICE trickle support in aiortc, this example
 may not work with Firefox. For details see:

 https://github.com/aiortc/aiortc/issues/481 and
 https://bugzilla.mozilla.org/show_bug.cgi?id=1691189

Webcam over RTSP
----------------

You can generate an RTSP stream from webcam by following these steps:

1. Run a local RTSP proxy server such as [mediamtx](https://github.com/bluenviron/mediamtx)

2. Capture video from webcam and stream it over RTSP. If you are an MacOS user and use mediamtx as RTSP proxy server, you can try this command. This will generate H264 encoded webcam video stream and send it to "localhost:8554" with stream name "webcam".

.. code-block:: console

    $ ffmpeg \
      -f avfoundation \
      -pix_fmt yuyv422 \
      -video_size 640x480 \
      -framerate 30 \
      -i "0:0" \
      -c:v h264_videotoolbox \
      -maxrate 2000k -bufsize 1000k \
      -an \
      -f rtsp -rtsp_transport tcp rtsp://localhost:8554/webcam
