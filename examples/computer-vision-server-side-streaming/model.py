from time import sleep, time

import cv2
import numpy as np


class MyModel:
    def __init__(self, runtime=0.1):
        self.runtime = runtime

    def infer(self, bgr):
        t0 = time()
        h, w = bgr.shape[:2]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1, 100, 100, 1, 20, minRadius=40, maxRadius=60)
        if circles is None or len(circles.shape) != 3:
            return np.array([-1, -1, -1], dtype="float32")

        # normalize:
        circle = circles[0][0]
        circle[0] /= w
        circle[1] /= h
        circle[2] /= w
        dt = time() - t0
        if dt < self.runtime:
            sleep(self.runtime - dt)
        return circle.astype("float32")

    def draw(self, bgr, circle):
        h, w = bgr.shape[:2]
        x, y, r = circle
        if x < 0:
            return
        x = int(x * w)
        y = int(y * h)
        r = int(r * w)
        cv2.rectangle(bgr, (x - r, y - r), (x + r, y + r), (0, 255, 0), 4)
