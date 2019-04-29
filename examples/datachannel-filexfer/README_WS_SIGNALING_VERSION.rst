=====
Important point
=====
- this version works with python on Windows environment! (tested only Windows10 64bit)
- uvloop pip package is not needed (the package does not support Windows platform)



=====
Run using websocket signaling server version example on a host
=====

- need three shell session

Procedure
--------
- First: exec Signaling server
- Second: exec Receiver
- Third: exed Sender

Signaling Server (using Websocket)
--------

.. code-block:: console

 $ python ws-signaling-server.py

Host-A (Sender)
--------

.. code-block:: console

 $ python filexfer-ws-signaling.py --signaling websocket --signaling-host 127.0.0.1 --signaling-port 8765 send <local filepath>

Host-B (Receiver)
--------

.. code-block:: console

 $ python filexfer-ws-signaling.py --signaling websocket --signaling-host 127.0.0.1 --signaling-port 8765 receive <local filepath to save>



=====
Run this example between two hosts on different networks which has NAT
=====

You can place personal signaling server on the internet with heroku!

Please visit `this repo`_ and press 'Deploy to Heroku' button!

``Deployed server is accessible``: --signaling-host <your-appname>.herokuapp.com --signaling-port 80

..  _this repo: https://github.com/ryogrid/punch_sctp_ws_signal_srv_for_sample

