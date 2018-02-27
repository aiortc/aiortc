import os.path
import sys

import setuptools

root_dir = os.path.abspath(os.path.dirname(__file__))
readme_file = os.path.join(root_dir, 'README.rst')
with open(readme_file, encoding='utf-8') as f:
    long_description = f.read()

setuptools.setup(
    name='aiortc',
    version='0.1.0',
    description='An implementation of WebRTC',
    long_description=long_description,
    url='https://github.com/jlaine/aiortc',
    author='Jeremy Lainé',
    author_email='jeremy.laine@m4x.org',
    license='BSD',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    packages=['aiortc'],
    install_requires=['aioice', 'crcmod', 'cryptography>=2.2.dev1', 'pyee', 'pylibsrtp'],
    dependency_links=[
        'git+https://github.com/pyca/cryptography.git@a36579b6e4086ded4c20578bbfbfae083d5e6bce#egg=cryptography-2.2.dev1',
    ]
)
