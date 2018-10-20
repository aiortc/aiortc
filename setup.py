import os.path

import setuptools

root_dir = os.path.abspath(os.path.dirname(__file__))
readme_file = os.path.join(root_dir, 'README.rst')
with open(readme_file, encoding='utf-8') as f:
    long_description = f.read()

cffi_modules = [
    'src/build_opus.py:ffibuilder',
    'src/build_vpx.py:ffibuilder',
]
install_requires = [
    'aioice>=0.6.9,<0.7.0',
    'attrs',
    'av>=6.0.0,<7.0.0',
    'cffi>=1.0.0',
    'crcmod',
    'cryptography>=2.2',
    'pyee',
    'pylibsrtp>=0.5.6',
    'pyopenssl',
]

if os.environ.get('READTHEDOCS') == 'True':
    cffi_modules = []
    install_requires = list(filter(lambda x: x != 'av', install_requires))

setuptools.setup(
    name='aiortc',
    version='0.9.12',
    description='An implementation of WebRTC and ORTC',
    long_description=long_description,
    url='https://github.com/jlaine/aiortc',
    author='Jeremy Lainé',
    author_email='jeremy.laine@m4x.org',
    license='BSD',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
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
    cffi_modules=cffi_modules,
    packages=['aiortc', 'aiortc.codecs', 'aiortc.contrib'],
    setup_requires=['cffi>=1.0.0'],
    install_requires=install_requires,
)
