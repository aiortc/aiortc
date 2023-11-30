import setuptools

setuptools.setup(
    cffi_modules=[
        "src/_cffi_src/build_opus.py:ffibuilder",
        "src/_cffi_src/build_vpx.py:ffibuilder",
    ]
)
