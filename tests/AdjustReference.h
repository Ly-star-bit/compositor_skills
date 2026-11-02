// AdjustReference.h: Compositor's pixel kernels (see AdjustReference.c), for the Swift side of the tool.
#ifndef AdjustReference_h
#define AdjustReference_h
#include <stdint.h>
#include <stddef.h>
void adjust_gradient_map(uint8_t *rgba, size_t width, size_t height, size_t stride, const uint8_t *table);
void adjust_grain(uint8_t *rgba, size_t width, size_t height, size_t stride, double amount, double size,
                  double roughness, uint32_t seed, double originX, double originY, double unitsPerPixel);
void adjust_black_white(uint8_t *rgba, size_t width, size_t height, size_t stride, const float *weights,
                        int tint, double tintHue, double tintSaturation);
void adjust_color_balance(uint8_t *rgba, size_t width, size_t height, size_t stride, const float *shadows,
                          const float *midtones, const float *highlights, int preserveLuminosity);
void levels_apply(uint8_t *pixels, size_t count, const float *tables);
void cube_apply(uint8_t *pixels, size_t count, const float *cube, int dimension);
void adjust_tonal_contrast(uint8_t *rgba, const uint8_t *blurred, size_t width, size_t height,
                           size_t stride, size_t blurredStride, double amount,
                           double shadows, double midtones, double highlights);
#endif
