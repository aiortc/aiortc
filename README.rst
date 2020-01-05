Style transfer server
====================================

Running
-------

Make sure pip and opencv are installed. If not, isntall:

.. code-block:: console

    $ apt install python3-pip python3-opencv


clone repo and install required packages:

.. code-block:: console

    $ git clone https://github.com/bbbrtk/aiortc.git
    $ apt install libavdevice-dev libavfilter-dev libopus-dev libvpx-dev pkg-config libsrtp2-dev
    $ pip3 install flask aiohttp aiortc 


to run modified aiortc server:

.. code-block:: console

    $ cd styletransfer/server
    $ python3 server.py
    $ # or to run in the backgorund
    $ nohup python3 server.py &

running on: http://your_ip:8080

to run flask server:

.. code-block:: console

    $ cd styletransfer/server/flask
    $ export FLASK_APP=server-flask.py
    $ nohup python -m flask run --host=0.0.0.0 &

running on: http://your_ip:5000/filter

To check if flask server works properly, use run flask/client-flask.py or send JSON: 

.. code-block:: console

    $ {
    $ "image" : "image encoded in base64",
    $ "benchmark" : "float 0-1",
    $ "color" : "boolean"
    $ }



aiortc
======

What is ``aiortc``?
-------------------

``aiortc`` is a library for `Web Real-Time Communication (WebRTC)`_ and
`Object Real-Time Communication (ORTC)`_ in Python. It is built on top of
``asyncio``, Python's standard asynchronous I/O framework.

The API closely follows its Javascript counterpart while using pythonic
constructs:

- promises are replaced by coroutines
- events are emitted using ``pyee.EventEmitter``

To learn more about ``aiortc`` please `read the documentation`_.

.. _Web Real-Time Communication (WebRTC): https://webrtc.org/
.. _Object Real-Time Communication (ORTC): https://ortc.org/
.. _read the documentation: https://aiortc.readthedocs.io/en/latest/


License
-------

``aiortc`` is released under the `BSD license`_.

.. _BSD license: https://aiortc.readthedocs.io/en/latest/license.html


Credits
-------

The audio file "demo-instruct.wav" was borrowed from the Asterisk
project. It is licensed as Creative Commons Attribution-Share Alike 3.0:

https://wiki.asterisk.org/wiki/display/AST/Voice+Prompts+and+Music+on+Hold+License
