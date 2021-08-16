#!/bin/bash
# installs and sets up the aiortc codebase
sudo apt update
sudo apt-get install -y libavdevice-dev libavfilter-dev libopus-dev libvpx-dev pkg-config libsrtp2-dev

# setup the package
pip install wheel
sudo python3 setup.py install

# retrieve the video
pip install -U youtube-dl
pip install opencv-python
/home/$USER/.local/bin/youtube-dl https://www.youtube.com/watch\?v\=gEDChDOM1_U\&vl\=en -o examples/videostream-cli/sundar_pichai.mp4
