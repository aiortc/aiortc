import os.path
import sys

import setuptools

root_dir = os.path.abspath(os.path.dirname(__file__))

about = {}
about_file = os.path.join(root_dir, "src", "aiortc", "about.py")
with open(about_file, encoding="utf-8") as fp:
    exec(fp.read(), about)

readme_file = os.path.join(root_dir, "README.rst")
with open(readme_file, encoding="utf-8") as f:
    long_description = f.read()

cffi_modules = [
    "src/_cffi_src/build_opus.py:ffibuilder",
    "src/_cffi_src/build_vpx.py:ffibuilder",
]
install_requires = [
    "aioice>=0.6.15,<0.7.0",
    "attrs",
    "av>=7.0.0,<8.0.0",
    "cffi>=1.0.0",
    "crc32c",
    "cryptography>=2.2",
    "pyee>=6.0.0",
    "pylibsrtp>=0.5.6",
]

datachannel_only = False

if os.environ.get("READTHEDOCS") == "True":
    readthedocs_build = True
    datachannel_only = True
else:
    readthedocs_build = False

if datachannel_only:
    cffi_modules = []
    install_requires = list(filter(lambda x: not x.startswith("av") and not x.startswith("cffi"), install_requires))

if datachannel_only and not readthedocs_build:
    about["__title__"] += "-datachannel"
    about["__summary__"] += " (datachannel-only version)"

setuptools.setup(
    name=about["__title__"],
    version=about["__version__"],
    description=about["__summary__"],
    long_description=long_description,
    url=about["__uri__"],
    author=about["__author__"],
    author_email=about["__email__"],
    license=about["__license__"],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
    cffi_modules=cffi_modules,
    package_dir={"": "src"},
    packages=["aiortc", "aiortc.codecs", "aiortc.contrib"],
    setup_requires=["cffi>=1.0.0"],
    install_requires=install_requires,
)
