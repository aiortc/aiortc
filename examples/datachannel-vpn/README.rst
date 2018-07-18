Data channel VPN
================

This example illustrates a layer2 VPN running over a WebRTC data channel.

First install the required packages:

.. code-block:: console

    $ pip install aiortc


Permissions
-----------

This example requires the CAP_NET_ADMIN capability in order to create and
configure network interfaces. There are two ways to achieve this:

- running the script as the root user. The downside is that the script will be
  run with higher privileges than actually needed.

- granting the CAP_NET_ADMIN capability to the Python interpreter. The downside
  is that *all* Python scripts will get this capability so you will almost
  certainly want to revert this change.

.. code-block:: console

    $ sudo setcap CAP_NET_ADMIN=ep /path/to/python3


Running
-------

On the first peer:

.. code-block:: console

    $ python3 vpn.py offer

On the second peer:

.. code-block:: console

    $ python3 vpn.py answer

Copy-and-paste the offer from the first peer to the second peer, then
copy-and-paste the answer from the second peer to the first peer.

A new network interface will be created on each peer. You can now setup these
interfaces by using the system's network tools:

.. code-block:: console

    $ ip address add 172.16.0.1/24 dev revpn-offer

and:

.. code-block:: console

    $ ip address add 172.16.0.2/24 dev revpn-answer
