#!/bin/bash
# installs and sets up the aiortc codebase
sudo apt update
sudo apt-get install -y libavdevice-dev libavfilter-dev libopus-dev libvpx-dev pkg-config libsrtp2-dev

# setup the package
pip install wheel
sudo python3 setup.py install
