import sys

import av

# | ffmpeg -y -f rawvideo -pixel_format rgb24 -video_size 1920x1080 -framerate 30 -i - -c:v libx264 -r 30 foo.mkv

# container = av.open(file="default:none", mode="r", format="avfoundation", options={"framerate": "30"})  # Mac webcam
container = av.open(file="/dev/video0", mode="r", format="v4l2",  # linux camera
                    options={"framerate": "30", "input_format": "mjpeg", "video_size": "1920x1080"})

frame_generator = container.decode(video=0)
while True:
    frame = next(frame_generator)
    sys.stdout.buffer.write(frame.to_image().tobytes())
    sys.stdout.flush()
