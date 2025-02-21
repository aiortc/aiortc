FROM pytorch/pytorch:2.4.0-cuda11.8-cudnn9-runtime

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    wget \
    pkg-config \
    python3-dev \
    yasm

# Build opus from source with PIC
WORKDIR /build
RUN wget https://archive.mozilla.org/pub/opus/opus-1.3.1.tar.gz && \
    tar xvf opus-1.3.1.tar.gz && \
    cd opus-1.3.1 && \
    CFLAGS="-fPIC" ./configure --enable-static --disable-shared && \
    make && \
    make install

# Build libvpx from source with PIC
RUN wget https://github.com/webmproject/libvpx/archive/refs/tags/v1.13.0.tar.gz && \
    tar xvf v1.13.0.tar.gz && \
    cd libvpx-1.13.0 && \
    CFLAGS="-fPIC" ./configure --enable-static --disable-shared && \
    make && \
    make install

# Install Python dependencies
RUN pip install wheel setuptools build cffi

# Setup workspace
WORKDIR /workspace
COPY . /workspace/

# Update library path
RUN ldconfig