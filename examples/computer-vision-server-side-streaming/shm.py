import ctypes
from multiprocessing import Lock, RawArray, RawValue

import numpy as np


class IndexedArray(object):
    def __init__(self, *, shape, dtype, ctype):
        self.shape = shape
        self.dtype = dtype
        self.array = RawArray(ctype, int(np.prod(shape)))
        self.idx = RawValue(ctypes.c_int64, 0)
        self.lock = Lock()

    def get(self, copy=False):
        with self.lock:
            arr = np.frombuffer(self.array, dtype=self.dtype)
            if self.shape is not None:
                arr = arr.reshape(self.shape)
            return self.idx.value, arr.copy() if copy else arr

    def set(self, idx, x):
        with self.lock:
            self.idx.value = idx
            arr = np.frombuffer(self.array, dtype=self.dtype)
            if self.shape is not None:
                arr = arr.reshape(self.shape)
            arr[:] = x


# def to_numpy_array(array, shape=None, dtype=np.uint8, copy=False):
#     with array.get_lock():
#         arr = np.frombuffer(array.get_obj(), dtype=dtype)
#         if shape is not None:
#             arr = arr.reshape(shape)
#         return arr.copy() if copy else arr
