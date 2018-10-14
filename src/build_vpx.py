from cffi import FFI
ffibuilder = FFI()

ffibuilder.set_source('aiortc.codecs._vpx', """
#include <vpx/vpx_decoder.h>
#include <vpx/vpx_encoder.h>
#include <vpx/vp8cx.h>
#include <vpx/vp8dx.h>

#undef vpx_codec_dec_init
#undef vpx_codec_enc_init

vpx_codec_err_t vpx_codec_dec_init(vpx_codec_ctx_t *ctx,
                                   vpx_codec_iface_t *iface,
                                   const vpx_codec_dec_cfg_t *cfg,
                                   vpx_codec_flags_t flags)
{
    return vpx_codec_dec_init_ver(ctx, iface, cfg, flags, VPX_DECODER_ABI_VERSION);
}

vpx_codec_err_t vpx_codec_enc_init(vpx_codec_ctx_t *ctx,
                                   vpx_codec_iface_t *iface,
                                   const vpx_codec_enc_cfg_t *cfg,
                                   vpx_codec_flags_t flags)
{
    return vpx_codec_enc_init_ver(ctx, iface, cfg, flags, VPX_ENCODER_ABI_VERSION);
}
    """,
    libraries=['vpx'])

ffibuilder.cdef("""
#define VPX_CODEC_USE_OUTPUT_PARTITION 0x20000
#define VPX_DL_REALTIME 1
#define VPX_EFLAG_FORCE_KF 1

#define VPX_FRAME_IS_KEY 0x1
#define VPX_FRAME_IS_DROPPABLE 0x2
#define VPX_FRAME_IS_INVISIBLE 0x4
#define VPX_FRAME_IS_FRAGMENT 0x8

#define VPX_PLANE_PACKED 0
#define VPX_PLANE_Y 0
#define VPX_PLANE_U 1
#define VPX_PLANE_V 2
#define VPX_PLANE_ALPHA 3

#define VP8_SET_POSTPROC 3
#define VP8E_SET_CPUUSED 13
#define VP8E_SET_NOISE_SENSITIVITY 15
#define VP8E_SET_STATIC_THRESHOLD 17
#define VP8E_SET_TOKEN_PARTITIONS 18

typedef enum {
  VPX_CODEC_OK,
  VPX_CODEC_ERROR,
  VPX_CODEC_MEM_ERROR,
  VPX_CODEC_ABI_MISMATCH,
  VPX_CODEC_INCAPABLE,
  VPX_CODEC_UNSUP_BITSTREAM,
  VPX_CODEC_UNSUP_FEATURE,
  VPX_CODEC_CORRUPT_FRAME,
  VPX_CODEC_INVALID_PARAM,
  VPX_CODEC_LIST_END
} vpx_codec_err_t;

enum vpx_codec_cx_pkt_kind {
  VPX_CODEC_CX_FRAME_PKT,
  ...
};

typedef enum vpx_img_fmt {
  VPX_IMG_FMT_I420,
  ...
} vpx_img_fmt_t;

typedef long vpx_codec_flags_t;
typedef uint32_t vpx_codec_frame_flags_t;
typedef long vpx_enc_frame_flags_t;
typedef const void *vpx_codec_iter_t;
typedef int64_t vpx_codec_pts_t;

typedef const struct vpx_codec_iface vpx_codec_iface_t;

typedef struct vpx_rational {
  int num;
  int den;
} vpx_rational_t;

enum vpx_rc_mode {
  VPX_VBR,
  VPX_CBR,
  VPX_CQ,
  VPX_Q,
};

enum vpx_kf_mode {
  VPX_KF_FIXED,
  VPX_KF_AUTO,
  VPX_KF_DISABLED = 0
};

typedef struct vpx_codec_dec_cfg {
  unsigned int threads;
  unsigned int w;
  unsigned int h;
} vpx_codec_dec_cfg_t;

typedef struct vpx_codec_enc_cfg {
  unsigned int g_usage;
  unsigned int g_threads;
  unsigned int g_profile;
  unsigned int g_w;
  unsigned int g_h;
  struct vpx_rational g_timebase;
  unsigned int g_lag_in_frames;

  unsigned int rc_resize_allowed;
  enum vpx_rc_mode rc_end_usage;
  unsigned int rc_target_bitrate;
  unsigned int rc_min_quantizer;
  unsigned int rc_max_quantizer;
  unsigned int rc_undershoot_pct;
  unsigned int rc_overshoot_pct;
  unsigned int rc_buf_sz;
  unsigned int rc_buf_initial_sz;
  unsigned int rc_buf_optimal_sz;

  enum vpx_kf_mode kf_mode;
  unsigned int kf_max_dist;
  ...;
} vpx_codec_enc_cfg_t;

typedef struct vpx_codec_ctx {
   ...;
} vpx_codec_ctx_t;

typedef struct vpx_fixed_buf {
  void *buf;
  size_t sz;
} vpx_fixed_buf_t;

typedef struct vpx_codec_cx_pkt {
  enum vpx_codec_cx_pkt_kind kind;
  union {
    struct {
      void *buf;
      size_t sz;
      vpx_codec_pts_t pts;
      unsigned long duration;
      vpx_codec_frame_flags_t flags;
      int partition_id;
    } frame;
    vpx_fixed_buf_t twopass_stats;
    vpx_fixed_buf_t firstpass_mb_stats;
    struct vpx_psnr_pkt {
      unsigned int samples[4];
      uint64_t sse[4];
      double psnr[4];
    } psnr;
    vpx_fixed_buf_t raw;
    char pad[124];
  } data;
  ...;
} vpx_codec_cx_pkt_t;

typedef struct vpx_image {
  vpx_img_fmt_t fmt;

  unsigned int w;
  unsigned int h;

  unsigned int d_w;
  unsigned int d_h;

  unsigned char *planes[4];
  int stride[4];
   ...;
} vpx_image_t;

enum vp8_postproc_level {
  VP8_NOFILTERING = 0,
  VP8_DEBLOCK = 1,
  VP8_DEMACROBLOCK = 2
};

typedef enum {
  VP8_ONE_TOKENPARTITION = 0,
  VP8_TWO_TOKENPARTITION = 1,
  VP8_FOUR_TOKENPARTITION = 2,
  VP8_EIGHT_TOKENPARTITION = 3
} vp8e_token_partitions;

typedef struct vp8_postproc_cfg {
  int post_proc_flag;
  int deblocking_level;
  int noise_level;
} vp8_postproc_cfg_t;

extern vpx_codec_iface_t *vpx_codec_vp8_cx(void);
extern vpx_codec_iface_t *vpx_codec_vp8_dx(void);
extern vpx_codec_iface_t *vpx_codec_vp9_cx(void);
extern vpx_codec_iface_t *vpx_codec_vp9_dx(void);

vpx_codec_err_t vpx_codec_control_(vpx_codec_ctx_t *ctx, int ctrl_id, ...);
vpx_codec_err_t vpx_codec_destroy(vpx_codec_ctx_t *ctx);

vpx_codec_err_t vpx_codec_dec_init(vpx_codec_ctx_t *ctx,
                                   vpx_codec_iface_t *iface,
                                   const vpx_codec_dec_cfg_t *cfg,
                                   vpx_codec_flags_t flags);

vpx_image_t *vpx_codec_get_frame(vpx_codec_ctx_t *ctx, vpx_codec_iter_t *iter);

vpx_codec_err_t vpx_codec_decode(vpx_codec_ctx_t *ctx, const uint8_t *data,
                                 unsigned int data_sz, void *user_priv,
                                 long deadline);

vpx_codec_err_t vpx_codec_enc_config_default(vpx_codec_iface_t *iface,
                                             vpx_codec_enc_cfg_t *cfg,
                                             unsigned int reserved);

vpx_codec_err_t vpx_codec_enc_config_set(vpx_codec_ctx_t *ctx,
                                         const vpx_codec_enc_cfg_t *cfg);

vpx_codec_err_t vpx_codec_enc_init(vpx_codec_ctx_t *ctx,
                                   vpx_codec_iface_t *iface,
                                   const vpx_codec_enc_cfg_t *cfg,
                                   vpx_codec_flags_t flags);

vpx_codec_err_t vpx_codec_encode(vpx_codec_ctx_t *ctx, const vpx_image_t *img,
                                 vpx_codec_pts_t pts, unsigned long duration,
                                 vpx_enc_frame_flags_t flags,
                                 unsigned long deadline);

const char *vpx_codec_err_to_string (vpx_codec_err_t err);

const vpx_codec_cx_pkt_t *vpx_codec_get_cx_data(vpx_codec_ctx_t *ctx,
                                                vpx_codec_iter_t *iter);

vpx_image_t *vpx_img_alloc(vpx_image_t *img, vpx_img_fmt_t fmt,
                           unsigned int d_w, unsigned int d_h,
                           unsigned int align);
void vpx_img_free(vpx_image_t *img);
vpx_image_t *vpx_img_wrap(vpx_image_t *img, vpx_img_fmt_t fmt, unsigned int d_w,
                          unsigned int d_h, unsigned int align,
                          unsigned char *img_data);
""")

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
