import asyncio
import fractions
import math
import random
from time import sleep, time

import cv2
import numpy as np
from av import VideoFrame

from aiortc import VideoStreamTrack


class MyVideoCapture:
    """
    You should replace with your `cv2.VideoCapture`. Here, we're mocking a circle random walking around the screen
    (so we can demo some 'computer vision' on it and the two approaches for serving your model results).
    """

    def __init__(self, height=1080, width=1920, radius=50, fps=25):
        self.fps = fps
        self.height = height
        self.width = width
        self.radius = radius
        self.x = None
        self.y = None
        self.start = 0

    def walk(self):
        if self.x is None:
            self.start = time()
            self.x = self.width / 2
            self.y = self.height / 2
        self.x += random.normalvariate(0, self.width / 100)
        self.y += random.normalvariate(0, self.height / 100)
        if self.x < self.radius:
            self.x = self.radius
        elif self.x > self.width - self.radius:
            self.x = self.width - self.radius
        if self.y < self.radius:
            self.y = self.radius
        elif self.y > self.height - self.radius:
            self.y = self.height - self.radius
        return int(self.x), int(self.y)

    def read(self):
        now = time()
        x, y = self.walk()
        bgr = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        cv2.circle(bgr, (x, y), self.radius, (255, 255, 255), 4)
        # we're going to yield this at the next frame ...
        frame_idx = int(math.ceil((now - self.start) * self.fps))
        time_at_frame_yield = self.start + frame_idx / self.fps
        # add time so we can compare latency:
        txt = f"#{frame_idx} @ {time_at_frame_yield}"
        txt_y = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 2, 2)[0][1]
        cv2.putText(bgr, txt, (10, 10 + txt_y), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 2)
        print(txt, end="\r")
        wait = time_at_frame_yield - time()
        if wait > 0:
            sleep(wait)
        return True, bgr


class SharedMemoryStreamTrack(VideoStreamTrack):
    def __init__(self, *, frame_array, fps, height, width):
        super().__init__()
        self.frame_array = frame_array
        self.height = height
        self.width = width
        self.fps = fps
        self._video_clock_rate = 90000
        self._video_ptime = 1 / self.fps
        self._video_time_base = fractions.Fraction(1, self._video_clock_rate)
        self._timestamp = None
        self._start = None

    async def next_timestamp(self):
        """
        We need to implement our own as the default just increments the timestamp by one frame (e.g. 1/25s for 25FPS)
        whereas we might be skipping frames - e.g. if our model means we're only processing at 1FPS, then we want to
        increment by 25 frames not just a single.
        """
        if self.readyState != "live":
            raise RuntimeError("Not live!")
        if self._timestamp is None:
            self._start = time()
            self._timestamp = 0
        else:
            # get the frame that this should be - note the ceil:
            dt = time() - self._start
            # get the timestamp in multiples of _video_clock_rate / fps - e.g. 90000 / 25 = 3600
            self._timestamp = int(int(dt * self._video_clock_rate / self.fps) * self.fps)
            # wait until the next frame is due:
            wait = self._timestamp / self._video_clock_rate - dt
            if wait > 0:
                await asyncio.sleep(wait)

        return self._timestamp, self._video_time_base

    async def recv(self):
        # get a frame different to the previous one:
        _, frame = self.frame_array.get(copy=True)
        # frame = np.ones(shape, dtype=np.uint8) * 255
        frame = VideoFrame.from_ndarray(frame, format="bgr24")
        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame
