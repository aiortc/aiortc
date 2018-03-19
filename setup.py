import os.path
import sys

import setuptools

root_dir = os.path.abspath(os.path.dirname(__file__))
readme_file = os.path.join(root_dir, 'README.rst')
with open(readme_file, encoding='utf-8') as f:
    long_description = f.read()

if os.environ.get('READTHEDOCS') == 'True':
    cffi_modules=[]
else:
    cffi_modules=[
        '_cffi_src/build_opus.py:ffibuilder',
        '_cffi_src/build_vpx.py:ffibuilder',
    ]

setuptools.setup(
    name='aiortc',
    version='0.4.1',
    description='An implementation of WebRTC and ORTC',
    long_description=long_description,
    url='https://github.com/jlaine/aiortc',
    author='Jeremy Lainé',
    author_email='jeremy.laine@m4x.org',
    license='BSD',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    cffi_modules=cffi_modules,
    packages=['aiortc', 'aiortc.codecs'],
    setup_requires=['cffi'],
    install_requires=['aioice>=0.5.1', 'attrs', 'crcmod', 'cryptography>=2.2', 'pyee', 'pylibsrtp', 'pyopenssl'],
)
