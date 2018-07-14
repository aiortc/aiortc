# VPN example

It is an prototype of layer2 vpn over webrtc. 

Install
-------

CAP_NET_ADMIN needed for creation and admin interfaces.

```
sudo setcap CAP_NET_ADMIN=ep $(readlink -f /usr/bin/python3) 
```
Or run it as root.


Running
-------

One peer:

```
python3 vpn.py offer
```

Another peer:

```
python3 vpn.py answer
```

Copy-paste json from offer One to Another, after copy-paste answer from Another to One.

Then setup network with system instruments. I.e.:

```
ip a a 172.16.0.1/24 dev revpn-offer
```

and

```
ip a a 172.16.0.2/24 dev revpn-answer
```
