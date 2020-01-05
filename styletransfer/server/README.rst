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

    $ python3 server.py
    $ # or to run in the backgorund
    $ nohup python3 server.py &

running on: http://your_ip:8080

to run flask server:

.. code-block:: console

    $ cd flask
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
