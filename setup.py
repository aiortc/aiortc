import os.path

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
    "aioice>=0.7.5,<0.8.0",
    "av>=9.0.0,<10.0.0",
    "cffi>=1.0.0",
    "cryptography>=2.2",
    'dataclasses; python_version < "3.7"',
    "google-crc32c>=1.1",
    "pyee>=9.0.0",
    "pylibsrtp>=0.5.6",
]

extras_require = {
    'dev': [
        'aiohttp>=3.7.0',
        'coverage>=5.0',
        'numpy>=1.19.0',
    ]
}

if os.environ.get("READTHEDOCS") == "True":
    cffi_modules = []
    install_requires = list(filter(lambda x: not x.startswith("av"), install_requires))

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
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
    ],
    cffi_modules=cffi_modules,
    package_dir={"": "src"},
    packages=["aiortc", "aiortc.codecs", "aiortc.contrib"],
    setup_requires=["cffi>=1.0.0"],
    install_requires=install_requires,
    extras_require=extras_require,
)
