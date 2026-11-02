/*
 * AdjustReference.c: Compositor's own pixel kernels for the adjustments the skill renders, copied unchanged from
 * github.com/robbietilton/Compositor at b4bfdea (Compositor/Rendering/AdjustPixels.c and LevelsPixels.c), so the
 * tests can check retouch_builder.py's ports of them.
 *
 * MIT License
 *
 * Copyright (c) 2026 Wonder Assembly LLC
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include "AdjustReference.h"

static inline uint32_t mix32(uint32_t x) {
    x ^= x >> 16;
    x *= 0x7feb352dU;
    x ^= x >> 15;
    x *= 0x846ca68bU;
    x ^= x >> 16;
    return x;
}

static inline float lattice(int64_t ix, int64_t iy, uint32_t seed) {
    uint32_t h = mix32((uint32_t)ix * 0x9E3779B1U ^ mix32((uint32_t)iy * 0x85EBCA77U ^ seed));
    return (float)(h & 0xFFFFU) / 65535.0f + (float)(h >> 16) / 65535.0f - 1.0f;
}

static inline float grain_field(double u, double v, double scale, uint32_t seed) {
    double cellX = floor(u / scale), cellY = floor(v / scale);
    float tx = (float)(u / scale - cellX), ty = (float)(v / scale - cellY);
    tx = tx * tx * (3.0f - 2.0f * tx);
    ty = ty * ty * (3.0f - 2.0f * ty);
    int64_t ix = (int64_t)cellX, iy = (int64_t)cellY;
    float n00 = lattice(ix, iy, seed), n10 = lattice(ix + 1, iy, seed);
    float n01 = lattice(ix, iy + 1, seed), n11 = lattice(ix + 1, iy + 1, seed);
    float top = n00 + (n10 - n00) * tx, bottom = n01 + (n11 - n01) * tx;
    // Blending neighboring lattice values narrows the spread; restore approximately its original range.
    return (top + (bottom - top) * ty) * 1.6f;
}

static inline float clamp255(float value) { return value < 0 ? 0 : value > 255 ? 255 : value; }

void adjust_gradient_map(uint8_t *rgba, size_t width, size_t height, size_t stride, const uint8_t *table) {
    for (size_t y = 0; y < height; y++) {
        uint8_t *p = rgba + y * stride;
        for (size_t x = 0; x < width; x++, p += 4) {
            unsigned a = p[3];
            if (a == 0) continue;
            unsigned r = p[0], g = p[1], b = p[2];
            if (a < 255) {
                r = (r * 255u + a / 2) / a;
                g = (g * 255u + a / 2) / a;
                b = (b * 255u + a / 2) / a;
                if (r > 255) r = 255;
                if (g > 255) g = 255;
                if (b > 255) b = 255;
            }
            unsigned level = (2126u * r + 7152u * g + 722u * b + 5000u) / 10000u;
            const uint8_t *color = table + (level > 255 ? 255 : level) * 3;
            p[0] = (uint8_t)((color[0] * a + 127u) / 255u);
            p[1] = (uint8_t)((color[1] * a + 127u) / 255u);
            p[2] = (uint8_t)((color[2] * a + 127u) / 255u);
        }
    }
}

void adjust_grain(uint8_t *rgba, size_t width, size_t height, size_t stride, double amount, double size,
                  double roughness, uint32_t seed, double originX, double originY, double unitsPerPixel) {
    if (!(amount > 0) || !(unitsPerPixel > 0)) return;
    if (!(size > 0)) size = 1;
    float strength = (float)(amount > 100 ? 1.0 : amount / 100.0) * 0.35f * 255.0f;
    float rough = (float)(roughness < 0 ? 0.0 : roughness > 100 ? 1.0 : roughness / 100.0);
    uint32_t fineSeed = mix32(seed ^ 0xA511E9B3U);
    // Roughness adds smaller, less regular particles, as in Photoshop, but their size remains
    // proportional to the Size control instead of collapsing to fixed one-pixel noise.
    double detailSize = fmax(0.5, size * 0.35);
    for (size_t y = 0; y < height; y++) {
        double v = originY + ((double)y + 0.5) * unitsPerPixel;
        uint8_t *p = rgba + y * stride;
        for (size_t x = 0; x < width; x++, p += 4) {
            unsigned a = p[3];
            if (a == 0) continue;
            double u = originX + ((double)x + 0.5) * unitsPerPixel;
            float smooth = grain_field(u, v, size, seed);
            float fine = grain_field(u, v, detailSize, fineSeed);
            float noise = smooth + (fine - smooth) * rough;
            float unpremultiply = a == 255 ? 1.0f : 255.0f / (float)a;
            float r = p[0] * unpremultiply, g = p[1] * unpremultiply, b = p[2] * unpremultiply;
            float level = (0.2126f * r + 0.7152f * g + 0.0722f * b) / 255.0f;
            if (level > 1) level = 1;
            // Film grain shows most in the midtones.
            float delta = noise * strength * (0.4f + 2.4f * level * (1.0f - level));
            float coverage = (float)a / 255.0f;
            p[0] = (uint8_t)(clamp255(r + delta) * coverage + 0.5f);
            p[1] = (uint8_t)(clamp255(g + delta) * coverage + 0.5f);
            p[2] = (uint8_t)(clamp255(b + delta) * coverage + 0.5f);
        }
    }
}

void adjust_black_white(uint8_t *rgba, size_t width, size_t height, size_t stride, const float *weights,
                        int tint, double tintHue, double tintSaturation) {
    for (size_t y = 0; y < height; ++y) {
        uint8_t *row = rgba + y * stride;
        for (size_t x = 0; x < width; ++x) {
            uint8_t *p = row + x * 4;
            float alpha = p[3];
            if (!alpha) continue;
            float r = p[0] * 255.0f / alpha, g = p[1] * 255.0f / alpha, b = p[2] * 255.0f / alpha;
            r = fminf(255.0f, r) / 255.0f; g = fminf(255.0f, g) / 255.0f; b = fminf(255.0f, b) / 255.0f;
            float mx = fmaxf(r, fmaxf(g, b)), mn = fminf(r, fminf(g, b));
            float md = r + g + b - mx - mn;
            // weights: 0 red, 1 yellow, 2 green, 3 cyan, 4 blue, 5 magenta
            int primary, secondary;
            if (mx == r)      { primary = 0; secondary = (g >= b) ? 1 : 5; }
            else if (mx == g) { primary = 2; secondary = (r >= b) ? 1 : 3; }
            else              { primary = 4; secondary = (g >= r) ? 3 : 5; }
            float gray = mn + (md - mn) * weights[secondary] + (mx - md) * weights[primary];
            gray = fminf(1.0f, fmaxf(0.0f, gray));
            float outR = gray, outG = gray, outB = gray;
            if (tint && tintSaturation > 0) {
                // The gray becomes the lightness of a color at the chosen hue.
                double c = (1.0 - fabs(2.0 * gray - 1.0)) * tintSaturation;
                double hp = fmod(tintHue, 360.0) / 60.0;
                double xx = c * (1.0 - fabs(fmod(hp, 2.0) - 1.0));
                double r1 = 0, g1 = 0, b1 = 0;
                if (hp < 1)      { r1 = c; g1 = xx; }
                else if (hp < 2) { r1 = xx; g1 = c; }
                else if (hp < 3) { g1 = c; b1 = xx; }
                else if (hp < 4) { g1 = xx; b1 = c; }
                else if (hp < 5) { r1 = xx; b1 = c; }
                else             { r1 = c; b1 = xx; }
                double m = gray - c / 2.0;
                outR = (float)fmin(1.0, fmax(0.0, r1 + m));
                outG = (float)fmin(1.0, fmax(0.0, g1 + m));
                outB = (float)fmin(1.0, fmax(0.0, b1 + m));
            }
            p[0] = (uint8_t)fminf(alpha, fmaxf(0.0f, roundf(outR * alpha)));
            p[1] = (uint8_t)fminf(alpha, fmaxf(0.0f, roundf(outG * alpha)));
            p[2] = (uint8_t)fminf(alpha, fmaxf(0.0f, roundf(outB * alpha)));
        }
    }
}

static void tonal_weights(float v, float *shadow, float *mid, float *highlight) {
    const float a = 0.25f, b = 0.333f, scale = 0.7f;
    float s = (v - b) / -a + 0.5f;
    float h = (v + b - 1.0f) / a + 0.5f;
    s = fminf(1.0f, fmaxf(0.0f, s));
    h = fminf(1.0f, fmaxf(0.0f, h));
    float m1 = fminf(1.0f, fmaxf(0.0f, (v - b) / a + 0.5f));
    float m2 = fminf(1.0f, fmaxf(0.0f, (v + b - 1.0f) / -a + 0.5f));
    *shadow = s * scale;
    *mid = m1 * m2 * scale;
    *highlight = h * scale;
}

void adjust_color_balance(uint8_t *rgba, size_t width, size_t height, size_t stride, const float *shadows,
                          const float *midtones, const float *highlights, int preserveLuminosity) {
    for (size_t y = 0; y < height; ++y) {
        uint8_t *row = rgba + y * stride;
        for (size_t x = 0; x < width; ++x) {
            uint8_t *p = row + x * 4;
            float alpha = p[3];
            if (!alpha) continue;
            float c[3];
            for (int i = 0; i < 3; ++i) c[i] = fminf(255.0f, p[i] * 255.0f / alpha) / 255.0f;
            float before = 0.299f * c[0] + 0.587f * c[1] + 0.114f * c[2];
            for (int i = 0; i < 3; ++i) {
                float s, m, h;
                tonal_weights(c[i], &s, &m, &h);
                c[i] += shadows[i] * s + midtones[i] * m + highlights[i] * h;
                c[i] = fminf(1.0f, fmaxf(0.0f, c[i]));
            }
            if (preserveLuminosity) {
                float after = 0.299f * c[0] + 0.587f * c[1] + 0.114f * c[2];
                if (after > 0.0001f) {
                    float ratio = before / after;
                    for (int i = 0; i < 3; ++i) c[i] = fminf(1.0f, fmaxf(0.0f, c[i] * ratio));
                }
            }
            for (int i = 0; i < 3; ++i) p[i] = (uint8_t)fminf(alpha, fmaxf(0.0f, roundf(c[i] * alpha)));
        }
    }
}

void levels_apply(uint8_t *pixels, size_t count, const float *tables) {
    for (size_t i=0; i<count; ++i) {
        uint8_t *p = pixels + i*4;
        float alpha = p[3];
        if (!alpha) continue;
        for (int channel=0; channel<3; ++channel) {
            float x = fminf(255, p[channel]*255.0f/alpha);
            int lo = (int)x, hi = lo < 255 ? lo+1 : 255;
            const float *table = tables + channel*256;
            float result = table[lo] + (table[hi]-table[lo])*(x-lo);
            p[channel] = (uint8_t)fminf(alpha, fmaxf(0, roundf(result*alpha)));
        }
    }
}

void cube_apply(uint8_t *pixels, size_t count, const float *cube, int dimension) {
    const float scale = (dimension - 1) / 255.0f;
    const size_t dy = dimension, dz = (size_t)dimension * dimension;
    for (size_t i=0; i<count; ++i) {
        uint8_t *p = pixels + i*4;
        float alpha = p[3];
        if (!alpha) continue;
        float position[3], fraction[3];
        int lo[3];
        for (int channel=0; channel<3; ++channel) {
            position[channel] = fminf(255, p[channel]*255.0f/alpha) * scale;
            lo[channel] = (int)position[channel];
            if (lo[channel] > dimension - 2) lo[channel] = dimension - 2;
            fraction[channel] = position[channel] - lo[channel];
        }
        const float *base = cube + ((size_t)lo[0] + lo[1]*dy + lo[2]*dz) * 4;
        const size_t sx = 4, sy = dy*4, sz = dz*4;
        for (int channel=0; channel<3; ++channel) {
            const float *c = base + channel;
            float x00 = c[0] + (c[sx] - c[0]) * fraction[0];
            float x10 = c[sy] + (c[sy+sx] - c[sy]) * fraction[0];
            float x01 = c[sz] + (c[sz+sx] - c[sz]) * fraction[0];
            float x11 = c[sz+sy] + (c[sz+sy+sx] - c[sz+sy]) * fraction[0];
            float y0 = x00 + (x10 - x00) * fraction[1];
            float y1 = x01 + (x11 - x01) * fraction[1];
            float result = y0 + (y1 - y0) * fraction[2];
            p[channel] = (uint8_t)fminf(alpha, fmaxf(0, roundf(result*alpha)));
        }
    }
}
