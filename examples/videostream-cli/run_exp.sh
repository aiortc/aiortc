#!/bin/sh
# Sample script to set up a sender playing from a video file
# and a receiver writing to a video file
# communicating via a unix socket for signaling
# prior to running this (especially after restarts), make sure to create a unix socket by running
# ```sudo python -c "import socket as s; sock = s.socket(s.AF_UNIX); sock.bind('/tmp/test.sock')```
# if successful, you should be able to view the received video and it should mimic
# the sent video until the point where you killed the process

python3 cli.py offer \
    --play-from ~/sundar_pichai.mp4 \
    --signaling-path /tmp/test.sock \
    --signaling unix-socket \
    --verbose 2>sender_output

sleep 5

python3 cli.py answer \
    --record-to received_video.mp4 \
    --signaling-path /tmp/test.sock \
    --signaling unix-socket \
    --verbose 2>receiver_output
