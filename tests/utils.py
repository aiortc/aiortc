import os


def load(name):
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, 'rb') as fp:
        return fp.read()
