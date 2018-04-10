Video channel CLI
================

This example builds on the Data channel CLI to also transmit a video stream.
The video stream is composed of three separate streams that have been combined
into a single stream that is three times as wide.

You may "copy and paste" the SDP into each window, otherwise it will read and
write to the local files `offer.json` or `answer.json`. The result of the video
stream is stored as `image.png`.

First install dependencies:

.. code:: bash

   pip install numpy opencv-python

To run the example, you will need instances of the `cli` example:

- The first takes on the role of the offerer. It generates an offer which you
  must copy-and-paste to the answerer. If `-d` is passed, the offer is also
  stored in the local directory as `offer.json`. If not answer is provided,
  then it will be read from `answer.json`.

.. code:: bash

   python cli.py offer -d

- The second takes on the role of the answerer. When given an offer, it will
  generate an answer which you must copy-and-paste to the offerer. If `-d` is
  passed and no offer is provided, then it will be read from `offer.json`. The
  answer is also stored in the local directory as `answer.json`.

.. code:: bash

   python cli.py answer -d

- Check `image.png` for the result of the stream.
