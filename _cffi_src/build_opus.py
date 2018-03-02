from cffi import FFI
ffibuilder = FFI()

ffibuilder.set_source('aiortc.codecs._opus', """
#include <opus/opus.h>
    """,
    libraries=['opus'])

ffibuilder.cdef("""
#define OPUS_APPLICATION_VOIP 2048

typedef struct OpusEncoder OpusEncoder;
typedef int16_t opus_int16;
typedef int32_t opus_int32;

OpusEncoder *opus_encoder_create(
    opus_int32 Fs,
    int channels,
    int application,
    int *error
);
opus_int32 opus_encode(
    OpusEncoder *st,
    const opus_int16 *pcm,
    int frame_size,
    unsigned char *data,
    opus_int32 max_data_bytes
);
void opus_encoder_destroy(OpusEncoder *st);
""")

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
