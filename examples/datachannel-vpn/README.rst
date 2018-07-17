Data channel VPN
================

This example illustrates a layer2 VPN running over a WebRTC data channel.

First install the required packages:

.. code-block:: console

    $ pip install aiortc


Permissions
-----------

The CAP_NET_ADMIN capability is needed to create and configure interfaces.

.. code-block:: console

    $ sudo setcap CAP_NET_ADMIN=ep $(readlink -f /usr/bin/python3)

Or run it as root.


Running
-------

One peer:

.. code-block:: console

    $ python3 vpn.py offer

Another peer:

.. code-block:: console

    $ python3 vpn.py answer

Copy-paste json from offer One to Another, after copy-paste answer from Another to One.

Then setup network with system instruments. I.e.:

.. code-block:: console

    $ ip a a 172.16.0.1/24 dev revpn-offer

and

.. code-block:: console
    $ ip a a 172.16.0.2/24 dev revpn-answer
