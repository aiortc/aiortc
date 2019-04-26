import os.path

import setuptools

root_dir = os.path.abspath(os.path.dirname(__file__))
readme_file = os.path.join(root_dir, 'README.rst')
with open(readme_file, encoding='utf-8') as f:
    long_description = f.read()

install_requires = [
    'aioice>=0.6.13,<0.7.0',
    'attrs',
    'crc32c',
    'cryptography>=2.2',
    'pyee',
    'pylibsrtp>=0.5.6',
    'pyopenssl'
]

setuptools.setup(
    name='aiortc',
    version='0.5.0',
    description='data channel feature only version of aiortc which implements WebRTC and ORTC',
    long_description=long_description,
    url='https://github.com/aiortc/aiortc',
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
        'Programming Language :: Python :: 3.7',
    ],
    packages=['aiortc', 'aiortc.contrib'],
    setup_requires=[],
    install_requires=install_requires,
)
