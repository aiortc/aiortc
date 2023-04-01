import os

import setuptools

cffi_modules = [
    "src/_cffi_src/build_opus.py:ffibuilder",
    "src/_cffi_src/build_vpx.py:ffibuilder",
]

# Do not build cffi modules on readthedocs as we lack the codec development files.
if os.environ.get("READTHEDOCS") == "True":
    cffi_modules = []

setuptools.setup(cffi_modules=cffi_modules)
