#!/usr/bin/env python3
"""
retouch_builder.py - layered portrait-retouching projects for Compositor (.comp).

Writes Compositor's project format, version 11 (Compositor 1.3+): PNG layers in images/ plus manifest.json; adds
to projects of versions up to 21 (Compositor 1.6), whose 16-bit PNG and OpenEXR layers it reads as 8-bit.
The manifest is checked against the rules Compositor applies on load (it ignores a project that breaks any
of them, silently) and only then swapped in atomically, so an open Compositor window reloads cleanly.

Each pixel layer is computed from what Compositor draws below the place it goes: the smoothing works on the photo
as it is under it, the glow on everything under it.

Layers are sorted into folders by what they do (GROUPS), bottom to top: frequency separation, skin, light (dual
curves, neutral gray, sculpt), features, the looks (a folder each), glow, then the grain and the observation
folders. Folders in Compositor are pass-through, so they change nothing in the picture.

Skin stack, right above the photo (in the frequency-separation and skin folders), in this order:
  --freq-sep        low/high frequency pair; high = (B - L)/2 + 128, so in Linear Light it rebuilds the photo
  --delicious-skin  DR5-style edge-preserving smoothing of the coarse tones only, pores kept: the form engine (default)
                    first heals the spots it finds (acne, small dark or red marks, with skin texture from next to
                    them), then uses He's guided filter on the skin alone, keeps the shadows around eyes, nostrils and
                    lips and fades round dark spots; `--delicious-skin manual` starts its mask black, to brush on as
                    in DR5's manual smoothing
On top:
  --sculpt          3D sculpt: a live Tonal Contrast layer on the face's planes (format 18+; before that a
                    mid-frequency contrast pixel layer in Soft Light)
  --neutral-gray    50% gray in Soft Light for dodge & burn with the brush
  --dual-curves     brighten and darken Curves adjustments in Luminosity, black masks
  --eyes            eye brilliance: a live Sharpen layer masked to the irises (format 19+; before that a
                    sharpened pixel copy)
  --look NAME       color grade, a folder of live adjustment layers: airy, film, cool-fair, creamy, hk, bw
  --glow [STYLE[:COLOR[,COLOR]]]
                    glow, repeatable: mist (highlights bloom, shadows stay deep) and halation (film halo) are Screen
                    layers computed from what is below, their strength in alpha and their color in a color overlay
                    effect (off = the light's own color), so it can be changed in Compositor; two colors make an
                    inner and an outer ring in a folder. dream is a live Gaussian Blur adjustment in Screen
  --grain           Grain adjustment
  --inspection      two hidden observation folders: Black & White with a contrast Curves (light and dark patches), and
                    color alone: a flat Curves in Luminosity under per-channel Curves (redness, casts, blotches)
Presets (--preset): daily = delicious-skin, neutral-gray, inspection (new or existing project); with --init only:
dr5 = freq-sep, delicious-skin, sculpt, dual-curves, eyes, grain, inspection; studio = freq-sep, neutral-gray,
dual-curves, inspection.
Run again on the same project, a module adds nothing twice: the neutral gray, sculpt, grain, a look of the same
style and the observation folders are skipped when the project has them, the dual curves fill in a missing half,
and the smoothing and eye-brilliance snapshots are made again in place with this run's settings (mask, id and
place kept); only the glow stacks, by design. --skin-strength sets the smoothing's strength apart from --strength.
Faces come from Apple Vision with landmarks, and the people in the photo from its person segmentation (a small
Swift helper, face_landmarks.swift, built once into ~/Library/Caches/compositor-retouch), so the masks for the eyes
and the sculpt layer come painted in and the skin mask keeps to the people; without Vision (no swiftc, not a Mac,
--no-vision) OpenCV finds the faces and those masks start black. Hair
and clothes are told from skin by Google's selfie multiclass segmentation model when the optional ai-edge-litert
package is installed (the 16 MB model is fetched once into the same cache folder), by texture otherwise or with
--no-parsing.
--refresh recomputes the smoothing and eye-brilliance snapshots in place from what is under them now.
--render flattens a project the way Compositor exports it (adjustment layers included, see `adjusted`) to an image.
--check validates an existing project without changing it. Check images (skin mask and healed spots, before/after,
painted masks, look, glow) go to a <Name>.checks folder next to the project.
"""

import argparse
import colorsys
import concurrent.futures
import contextlib
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid

try:
    import numpy as np
    # Compositor keeps an HDR layer as OpenEXR (format 13); OpenCV decodes that only when told to before it loads.
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    import cv2
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError as error:
    sys.exit(f"[Error] missing Python package '{error.name}'. Install the dependencies with:\n"
             f"  python3 -m pip install 'opencv-python-headless<5' pillow numpy")

FORMAT = "com.compositor.project"
FORMAT_VERSION = 11
# The newest format this skill knows (Compositor 1.6, November 2026): 16-bit PNG layers from version 12, OpenEXR
# (HDR) layers from 13, shape and GIF layers 14-17, the Filter menu's adjustment kinds 16-19 and 21 (Reduce Noise), a
# Prism setting in 20. Layers this skill adds are the kinds every version since 9 takes; a project keeps its own
# version number.
NEWEST_VERSION = 21
# Where the files a run writes wait until commit: a hidden folder in the package, outside images/ (see Project._stage).
STAGING = ".retouch"
MAX_SIDE = 30_000
# Level 3 encodes several times faster than Pillow's default 6 for a few percent more bytes;
# Compositor re-encodes the layers itself the next time the project is saved there.
PNG_COMPRESS_LEVEL = 3
OBSERVATION_PREFIX = "🔍 观察组"
GRAIN_PREFIX = "🎞️"
GLOW_PREFIX = "✨"
LOOK_PREFIX = "🎬"
# How the skin-stack layers' names begin. Compositor drops unknown manifest keys when it saves, so the name is what
# marks a layer as one of these; renaming one only changes where later skin layers are placed. HEAL, DODGE_BURN,
# FOLDS, SHINE and EVEN_TONE are layers earlier versions of this skill made (automatic healing, a gray dodge & burn,
# softened creases, shine and color evening): still recognized, so new skin layers go above them in a project from then.
FS_LOW, FS_HIGH = "低频", "高频"
HEAL, SMOOTH, DODGE_BURN, FOLDS, SHINE, EVEN_TONE = "🩹", "🔥", "🌗", "〰️", "💧", "🎨"
SKIN_STACK = (FS_LOW, FS_HIGH, HEAL, SMOOTH, DODGE_BURN, FOLDS, SHINE, EVEN_TONE)
# The folders layers are sorted into by what they do, bottom to top: (group, the folder's name, or None for a group
# whose layers sit at the top level, how the names of its loose layers begin). Like a skin layer, a folder is known by
# how its name begins: its emoji (folder_mark), which no layer name starts with. The loose names are also those of
# the layers earlier versions of this skill left at the top level, so new folders go next to them. The
# frequency-separation and skin folders sit right above the photo (Project.skin_walk); the others go in their group's
# place among the top-level records (Project.put). A look is a folder of its own, its opacity the grade's strength;
# the grain and the observation folder stay loose on top. The features group's 🌙 👀 🦷 💄 are layers earlier
# versions made (dark circles, eye whites, teeth, lips), kept so a new folder goes next to them.
GROUPS = (
    ("freq_sep", "🎚️ 高低频 (低频修肤色光影 · 高频修纹理瑕疵)", (FS_LOW, FS_HIGH)),
    ("skin", "🧴 皮肤 (磨皮 · 关掉文件夹对比修前)", SKIN_STACK[2:]),
    ("light", "🌓 光影 (双曲线 · 中性灰 · 骨相)", ("🗿", "🩶", "🌑", "☀️")),
    ("features", "😊 五官", ("🌙", "👁️", "👀", "🦷", "💄")),
    ("look", None, (LOOK_PREFIX,)),
    ("glow", "🌟 辉光 (文件夹不透明度调整体强弱)", (GLOW_PREFIX,)),
    ("grain", None, (GRAIN_PREFIX,)),
    ("inspection", None, (OBSERVATION_PREFIX,)),
)
RANK = {key: rank for rank, (key, _, _) in enumerate(GROUPS)}
FOLDER_NAMES = {key: name for key, name, _ in GROUPS if name}
MAX_SPOTS = 80

BLEND_MODES = {
    "Normal", "Darken", "Multiply", "Color Burn", "Linear Burn", "Lighten", "Screen", "Color Dodge",
    "Linear Dodge (Add)", "Overlay", "Soft Light", "Hard Light", "Vivid Light", "Linear Light", "Pin Light",
    "Hard Mix", "Difference", "Exclusion", "Subtract", "Divide", "Hue", "Saturation", "Color", "Luminosity",
}
ADJUSTMENT_KINDS = {
    "Hue/Saturation", "Levels", "Curves", "Exposure", "Gradient Map", "Grain", "Add Noise", "Gaussian Blur",
    "Motion Blur", "Invert", "Black & White", "Color Balance",
    "Progressive Blur", "Bloom / Glow", "RGB Split", "Vignette", "Lens Effects", "Tonal Contrast", "Sharpen",
    "Reduce Noise",
}
# The format version each adjustment kind arrived in (7 for the rest), and the version each block inside an
# adjustment's filterLayerSettings needs (the object itself 16).
KIND_VERSIONS = {
    "Gaussian Blur": 9, "Motion Blur": 9, "Add Noise": 9, "Progressive Blur": 16, "Bloom / Glow": 16, "RGB Split": 16,
    "Vignette": 18, "Lens Effects": 18, "Tonal Contrast": 18, "Sharpen": 19, "Reduce Noise": 21,
}
FILTER_BLOCK_VERSIONS = {"vignette": 18, "tonal": 18, "lens": 18, "sharpen": 19, "reduceNoise": 21}
SAMPLINGS = {"Nearest", "Smooth", "High quality"}
CHANNELS = {"RGB", "Red", "Green", "Blue"}
UUID_PATTERN = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


class BuildError(Exception):
    """Stops the build: printed to stderr, exit status 1, and the project is left as it was."""


def say(message, stream=None):
    """Prints a line. A reader that stops listening early (`| head`) must not abort a build half-way."""
    stream = stream or sys.stdout
    try:
        print(message, file=stream)
    except BrokenPipeError:
        try:  # send the rest to /dev/null so the flush at exit doesn't fail again
            os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())
        except (OSError, ValueError):
            pass


_warned = set()


def warn(message, once=False):
    if once:
        if message in _warned:
            return
        _warned.add(message)
    say(f"[WARN] {message}", sys.stderr)


def new_id():
    return str(uuid.uuid4()).upper()


def odd(value):
    return int(value) | 1


def to8(image):
    """0-1 floats to 8-bit."""
    return np.clip(np.rint(image * 255.0), 0, 255).astype(np.uint8)


def canvas_transform(width, height):
    # Every key is required: Compositor decodes origin/size as [x, y] / [w, h] arrays and rejects missing keys.
    return {"origin": [0, 0], "size": [width, height], "rotation": 0,
            "flipX": False, "flipY": False, "sampling": "High quality"}


def solid_mask(value=0):
    """A uniform 1x1 mask. Compositor stretches it over the layer and allocates pixels only once it's painted."""
    return Image.new("L", (1, 1), value)


def fs_radius(width, height):
    """A radius for the resolution: about pore size on a typical portrait."""
    return max(2.5, round(min(width, height) / 700.0 * 1.5, 1))


# Frequency separation splits at this share of the face's width: pores, fine lines and small blemishes go to the high
# layer, skin tone, blotches and the face's light and shade stay in the low one, whatever the framing (a 24 MP
# headshot gets about what the resolution used to give it; a half-length or group photo no longer splits its faces'
# shading off into the high layer).
FS_FACE = 200.0


# ==========================================
# 调整层 JSON (Adjustment records, shaped exactly as Compositor encodes them)
# ==========================================

def adjustment(kind, **settings):
    """Every kind carries identity levels and curves blocks plus its own settings."""
    record = {
        "kind": kind, "hue": 0, "saturation": 0, "lightness": 0, "colorize": False,
        "levels": {"channel": "RGB", "ranges": [
            {"black": 0, "gamma": 1, "white": 255, "outputBlack": 0, "outputWhite": 255} for _ in range(4)]},
        "curves": {"channel": "RGB", "channels": [
            [{"x": 0, "y": 0}, {"x": 255, "y": 255}] for _ in range(4)]},
    }
    record.update(settings)
    return record


def curves(points):
    """A Curves adjustment with `points` on the RGB curve (x from 0 to 255, increasing)."""
    record = adjustment("Curves")
    record["curves"]["channels"][0] = [{"x": x, "y": y} for x, y in points]
    return record


# A filter adjustment layer's `filterLayerSettings` (version 16+). FilterLayerSettings is decoded by Swift's synthesized
# Codable, so every one of these shared fields must be present, at a value equal to its clamped self, or Compositor
# refuses the whole project; they are Compositor's defaults. A filter's own numbers go in a block of their own
# (`tonal`, `sharpen`; version 18 and 19), within these ranges.
FILTER_SHARED = {
    "progressiveFrom": 0, "progressiveTo": 20, "progressiveRadial": False, "progressiveInverted": False,
    "bloomAmount": 40, "bloomRadius": 24, "rgbSplitDistance": 10, "rgbSplitBlur": 0, "rgbSplitAngle": 0,
    "rgbSplitRadial": False, "rgbSplitReach": 1000,
}
FILTER_SHARED_RANGES = {
    "progressiveFrom": (0, 250), "progressiveTo": (0, 250), "bloomAmount": (0, 100), "bloomRadius": (1, 150),
    "rgbSplitDistance": (0, 200), "rgbSplitBlur": (0, 400), "rgbSplitAngle": (-180, 180), "rgbSplitReach": (1, 100_000),
}
TONAL_RANGES = {"amount": (0, 100), "radius": (1, 100), "shadows": (-100, 100), "midtones": (-100, 100), "highlights": (-100, 100)}
SHARPEN_RANGES = {"amount": (0, 300), "radius": (0.1, 20)}
REDUCE_NOISE_RANGES = {"strength": (0, 100), "details": (0, 100), "color": (0, 100)}  # checked, never written
TONAL_DEFAULTS = {"amount": 50, "radius": 16, "shadows": 40, "midtones": 60, "highlights": 30}
SHARPEN_DEFAULTS = {"amount": 100, "radius": 1}


def filter_layer(**blocks):
    return {**FILTER_SHARED, **blocks}


def tonal_contrast(amount=50, radius=16, shadows=40, midtones=60, highlights=30):
    """A Tonal Contrast adjustment (version 18): local contrast from the brightness's difference to a Gaussian blur of
    `radius`, added back at `amount` (50 = Compositor's unit strength), weighted per tone (-100 to 100)."""
    return adjustment("Tonal Contrast", filterLayerSettings=filter_layer(tonal={
        "amount": amount, "radius": radius, "shadows": shadows, "midtones": midtones, "highlights": highlights}))


def sharpen(amount=100, radius=1):
    """A Sharpen adjustment (version 19): an unsharp mask of `radius` pixels at `amount` percent."""
    return adjustment("Sharpen", filterLayerSettings=filter_layer(sharpen={"amount": amount, "radius": radius}))


# Photoshop's default hue bands (falloff start, range start, range end, falloff end), as Compositor's.
HUE_BANDS = {
    "Master": (0, 0, 360, 360), "Reds": (315, 345, 15, 45), "Yellows": (15, 45, 75, 105),
    "Greens": (75, 105, 135, 165), "Cyans": (135, 165, 195, 225), "Blues": (195, 225, 255, 285),
    "Magentas": (255, 285, 315, 345),
}


def hue_saturation(selected, ranges):
    """Hue/Saturation per color range: `ranges` maps a range name to (hue, saturation, lightness).
    Swift encodes these enum-keyed dictionaries as flat [key, value, key, value, ...] arrays."""
    adjustments, bands = [], []
    for name, (hue, saturation, lightness) in ranges.items():
        adjustments += [name, {"hue": hue, "saturation": saturation, "lightness": lightness}]
    for name, (falloff_start, range_start, range_end, falloff_end) in HUE_BANDS.items():
        bands += [name, {"falloffStart": falloff_start, "rangeStart": range_start,
                         "rangeEnd": range_end, "falloffEnd": falloff_end}]
    return adjustment("Hue/Saturation", hsvSettings={
        "range": selected, "colorize": False, "invertRange": False, "adjustments": adjustments, "bands": bands})


# ==========================================
# 混合 (Blending, as Compositor draws pixel layers; RGB floats 0-1)
# ==========================================

def luma(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _lum(c):  # the luminosity the non-separable modes (Color, Luminosity) use
    return c[..., 0] * 0.3 + c[..., 1] * 0.59 + c[..., 2] * 0.11


def _set_lum(color, target):
    color = color + (target - _lum(color))[..., None]
    lum = _lum(color)[..., None]
    low = color.min(axis=-1, keepdims=True)
    high = color.max(axis=-1, keepdims=True)
    color = np.where(low < 0, lum + (color - lum) * lum / np.maximum(lum - low, 1e-6), color)
    return np.where(high > 1, lum + (color - lum) * (1 - lum) / np.maximum(high - lum, 1e-6), color)


def soft_light(backdrop, source):
    """The W3C soft light, which Core Image's (what Compositor uses) follows."""
    lifted = np.where(backdrop <= 0.25, ((16 * backdrop - 12) * backdrop + 4) * backdrop, np.sqrt(backdrop))
    return np.where(source <= 0.5,
                    backdrop - (1 - 2 * source) * backdrop * (1 - backdrop),
                    backdrop + (2 * source - 1) * (lifted - backdrop))


def _sat(c):
    return c.max(axis=-1) - c.min(axis=-1)


def _set_sat(color, saturation):
    """The W3C SetSat: `color` keeping its hue, with the given saturation (max - min)."""
    high, low = color.max(axis=-1, keepdims=True), color.min(axis=-1, keepdims=True)
    span = high - low
    scaled = (color - low) * (saturation[..., None] / np.maximum(span, 1e-6))
    return np.where(span > 1e-6, scaled, 0.0)


def _dodge(b, s):
    return np.where(b <= 0, 0.0, np.where(s >= 1, 1.0, np.minimum(1, b / np.maximum(1 - s, 1e-6))))


def _burn(b, s):
    return np.where(b >= 1, 1.0, np.where(s <= 0, 0.0, 1 - np.minimum(1, (1 - b) / np.maximum(s, 1e-6))))


# The W3C compositing formulas (what Core Graphics and Core Image follow), with Photoshop's for the modes they lack.
BLENDS = {
    "Normal": lambda b, s: s,
    "Darken": lambda b, s: np.minimum(b, s),
    "Multiply": lambda b, s: b * s,
    "Color Burn": _burn,
    "Linear Burn": lambda b, s: np.clip(b + s - 1, 0, 1),
    "Lighten": lambda b, s: np.maximum(b, s),
    "Screen": lambda b, s: b + s - b * s,
    "Color Dodge": _dodge,
    "Linear Dodge (Add)": lambda b, s: np.minimum(1, b + s),
    "Overlay": lambda b, s: np.where(b <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s)),
    "Soft Light": soft_light,
    "Hard Light": lambda b, s: np.where(s <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s)),
    "Vivid Light": lambda b, s: np.where(s <= 0.5, _burn(b, 2 * s), _dodge(b, 2 * s - 1)),
    "Linear Light": lambda b, s: np.clip(b + 2 * s - 1, 0, 1),
    "Pin Light": lambda b, s: np.where(s <= 0.5, np.minimum(b, 2 * s), np.maximum(b, 2 * s - 1)),
    "Hard Mix": lambda b, s: np.where(b + s >= 1, 1.0, 0.0),
    "Difference": lambda b, s: np.abs(b - s),
    "Exclusion": lambda b, s: b + s - 2 * b * s,
    "Subtract": lambda b, s: np.maximum(0, b - s),
    "Divide": lambda b, s: np.where(s <= 0, np.where(b > 0, 1.0, 0.0), np.minimum(1, b / np.maximum(s, 1e-6))),
    "Hue": lambda b, s: _set_lum(_set_sat(s, _sat(b)), _lum(b)),
    "Saturation": lambda b, s: _set_lum(_set_sat(b, _sat(s)), _lum(b)),
    "Color": lambda b, s: _set_lum(s, _lum(b)),
    "Luminosity": lambda b, s: _set_lum(b, _lum(s)),
}


def blend(backdrop, source, alpha, mode):
    """`source` over `backdrop` in `mode`, with per-pixel coverage `alpha`."""
    result = BLENDS[mode](backdrop, source)
    coverage = alpha[..., None]
    return np.clip(backdrop * (1 - coverage) + result * coverage, 0, 1).astype(np.float32)


# ==========================================
# 输入照片 (Loading the photo)
# ==========================================

def to_uint8(array):
    """8 bits per channel from 16-bit, 32-bit or float pixel data."""
    if array.dtype == np.uint8:
        return array
    if np.issubdtype(array.dtype, np.floating):
        peak = float(np.nanmax(array)) if array.size else 1.0
        scale = 255.0 if peak <= 1.0 else 1.0
        return np.clip(np.rint(np.nan_to_num(array) * scale), 0, 255).astype(np.uint8)
    wide = np.clip(array.astype(np.int64), 0, 65535)
    return ((wide * 255 + 32767) // 65535).astype(np.uint8)


def to_srgb(image, icc):
    """Converts from an embedded color profile to sRGB, the only working space Compositor accepts."""
    if not icc:
        return image
    try:
        from PIL import ImageCms
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        name = (ImageCms.getProfileDescription(source) or "").strip()
        if "srgb" in name.lower() and image.mode != "CMYK":
            return image
        intent = getattr(getattr(ImageCms, "Intent", None), "RELATIVE_COLORIMETRIC", 1)
        converted = ImageCms.profileToProfile(image, source, ImageCms.createProfile("sRGB"),
                                              renderingIntent=intent,
                                              outputMode="RGBA" if image.mode == "RGBA" else "RGB")
        say(f"[OK] Converted colors from '{name}' to sRGB")
        return converted
    except Exception as error:  # a broken or unsupported profile shouldn't stop the build
        warn(f"could not convert the embedded color profile to sRGB ({error}); colors may shift in Compositor")
        return image


def load_photo(path):
    """The photo as upright 8-bit sRGB RGBA, plus its resolution in pixels per inch."""
    if not os.path.isfile(path):
        raise BuildError(f"input image not found: {path}")
    try:
        image = Image.open(path)
        image.load()
    except Exception as error:
        array = cv2.imread(path, cv2.IMREAD_UNCHANGED)  # some 16-bit or float TIFFs Pillow can't decode
        if array is None:
            raise BuildError(f"cannot read {path} ({error}). JPEG, PNG and TIFF are supported; convert HEIC or RAW "
                             f"first, e.g. `sips -s format tiff photo.heic --out photo.tif`")
        array = to_uint8(array)
        if array.ndim == 2:
            return Image.fromarray(array).convert("RGBA"), 72
        code = cv2.COLOR_BGRA2RGBA if array.shape[2] == 4 else cv2.COLOR_BGR2RGB
        return Image.fromarray(cv2.cvtColor(array, code)).convert("RGBA"), 72

    dpi = image.info.get("dpi")
    image = ImageOps.exif_transpose(image)  # camera JPEGs are often stored sideways
    icc = image.info.get("icc_profile")
    has_alpha = image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in image.info
    if image.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        image = Image.fromarray(to_uint8(np.asarray(image)))
    if image.mode in ("RGB", "RGBA", "CMYK"):
        image = to_srgb(image, icc)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if has_alpha else "RGB")

    resolution = 72
    if dpi and isinstance(dpi[0], (int, float)) and 50 <= float(dpi[0]) <= 9600:
        resolution = round(float(dpi[0]), 2)
    return image.convert("RGBA"), resolution


def deep_photo(path):
    """Whether a photo has more than 8 bits a channel: a 16-bit PNG, a 16-bit or float TIFF, or a file Pillow can't
    decode at all (the 16-bit and float TIFFs it doesn't support)."""
    try:
        if png_header(path)[2] == 16:
            return True
    except (OSError, ValueError):
        pass
    try:
        with Image.open(path) as image:
            if image.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
                return True
            if image.format == "TIFF":
                bits = image.tag_v2.get(258, 8)
                return (max(bits) if isinstance(bits, tuple) else bits) > 8
            return False
    except Exception:
        return True


def import_deep(photo):
    """A photo of more than 8 bits a channel as Compositor imports one: the Apple helper (face_landmarks.swift,
    --import) turns it upright and converts it to sRGB at 16 bits. Returns the 16-bit PNG it wrote, in a temporary
    folder the caller removes, and its pixels (uint16 RGBA); None, after a warning, when that can't be done."""
    tool = vision_tool()
    if tool is None:
        warn("the photo has more than 8 bits a channel, but without Apple's tools (swiftc) it becomes an 8-bit layer")
        return None
    folder = tempfile.mkdtemp(prefix="compositor-retouch-")
    png = os.path.join(folder, "photo.png")
    try:
        result = subprocess.run([tool, "--import", os.path.abspath(photo), png], capture_output=True, text=True,
                                timeout=600)
        pixels = cv2.imdecode(np.fromfile(png, np.uint8), cv2.IMREAD_UNCHANGED) if result.returncode == 0 else None
        why = result.stderr.strip()[-200:]
    except (OSError, subprocess.TimeoutExpired) as error:
        pixels, why = None, str(error)
    if pixels is None or pixels.dtype != np.uint16 or pixels.ndim != 3 or pixels.shape[2] not in (3, 4):
        shutil.rmtree(folder, ignore_errors=True)
        warn(f"could not keep the photo's 16 bits ({why or 'no 16-bit image came back'}); it becomes an 8-bit layer")
        return None
    if pixels.shape[2] == 3:
        pixels = np.dstack([pixels, np.full(pixels.shape[:2], 65535, np.uint16)])
    return png, cv2.cvtColor(pixels, cv2.COLOR_BGRA2RGBA)


def read_image(path):
    """A layer's image or mask file, loaded. A 16-bit PNG layer (format 12) or an OpenEXR one (format 13: straight,
    linear sRGB, white at 1) comes as 8-bit sRGB RGBA, highlights clipped to white, as Compositor shows and edits it."""
    if path.endswith(".exr"):
        pixels = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
        if pixels is None:
            raise OSError(f"cannot decode {os.path.basename(path)} as OpenEXR")
        pixels = np.nan_to_num(pixels.astype(np.float32))
        color, alpha = (pixels[..., :3], pixels[..., 3]) if pixels.ndim == 3 and pixels.shape[2] == 4 else (pixels, None)
        color = color[..., ::-1] if color.ndim == 3 else np.dstack([color] * 3)  # OpenCV gives BGR
        alpha = np.ones(color.shape[:2], np.float32) if alpha is None else np.clip(alpha, 0, 1)
        return Image.fromarray(np.dstack([to8(linear_to_srgb(color)), to8(alpha)]), "RGBA")
    try:
        depth = png_header(path)[2]
    except (OSError, ValueError):
        depth = 8
    if depth == 16:
        pixels = to_uint8(cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED))
        if pixels.ndim == 2:
            return Image.fromarray(pixels).convert("RGBA")
        code = cv2.COLOR_BGRA2RGBA if pixels.shape[2] == 4 else cv2.COLOR_BGR2RGB
        return Image.fromarray(cv2.cvtColor(pixels, code)).convert("RGBA")
    image = Image.open(path)
    image.load()
    return image


# ==========================================
# 滤波与蒙版 (Filters, faces, skin)
# ==========================================

def gaussian(image, sigma):
    """Gaussian blur. Wide ones run on a reduced copy: they only carry low frequencies, and at 24 MP a full-size
    kernel hundreds of pixels across takes many seconds."""
    if sigma <= 8:
        return cv2.GaussianBlur(image, (0, 0), sigma)
    height, width = image.shape[:2]
    factor = max(2, int(sigma // 4))
    small = cv2.resize(image, (max(1, width // factor), max(1, height // factor)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / factor)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def masked_blur(image, weight, sigma):
    """A blur that averages only where `weight` (the skin) is, so eyes, lips, hair and background don't bleed in.
    Where no skin is near, the image is left as it is."""
    support = gaussian(weight, sigma)
    if image.ndim == 3:
        weight, support = weight[..., None], support[..., None]
    blurred = gaussian(image * weight, sigma) / np.maximum(support, 1e-4)
    return np.where(support > 0.02, blurred, image)


def crop_box(mask, pad):
    """Rows and columns around the mask's non-zero pixels, padded, as slices."""
    points = cv2.findNonZero((mask > 0).astype(np.uint8))
    if points is None:
        return slice(0, mask.shape[0]), slice(0, mask.shape[1])
    x, y, w, h = cv2.boundingRect(points)
    pad = int(pad)
    return (slice(max(0, y - pad), min(mask.shape[0], y + h + pad)),
            slice(max(0, x - pad), min(mask.shape[1], x + w + pad)))


def guided_filter(image, radius, eps):
    """He et al.'s guided filter on each RGB channel, guided by luminance. Large photos run it subsampled
    (He & Sun 2015, "Fast Guided Filter"): the linear coefficients are smooth, so they upsample cleanly."""
    height, width = image.shape[:2]
    guide = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    scale = max(1, int(round(min(height, width) / 1000.0)))
    if scale > 1:
        small = (max(1, width // scale), max(1, height // scale))
        g = cv2.resize(guide, small, interpolation=cv2.INTER_AREA)
        p = cv2.resize(image, small, interpolation=cv2.INTER_AREA)
        radius = max(1, int(round(radius / scale)))
    else:
        g, p = guide, image
    size = (2 * radius + 1, 2 * radius + 1)
    mean_g = cv2.boxFilter(g, -1, size)
    var_g = cv2.boxFilter(g * g, -1, size) - mean_g * mean_g
    result = np.empty_like(image)
    for channel, plane in enumerate(cv2.split(p)):
        mean_p = cv2.boxFilter(plane, -1, size)
        a = (cv2.boxFilter(g * plane, -1, size) - mean_g * mean_p) / (var_g + eps)
        b = mean_p - a * mean_g
        mean_a = cv2.boxFilter(a, -1, size)
        mean_b = cv2.boxFilter(b, -1, size)
        if scale > 1:
            mean_a = cv2.resize(mean_a, (width, height), interpolation=cv2.INTER_LINEAR)
            mean_b = cv2.resize(mean_b, (width, height), interpolation=cv2.INTER_LINEAR)
        result[:, :, channel] = mean_a * guide + mean_b
    return result


def person_matte(small, rgb):
    """Vision's person matte (8-bit, at its own resolution) at the photo's size, its edges drawn to the photo's own
    (see upsample_guided): a plain enlargement's rim of backdrop counted as person would halo the hair. 0-1."""
    return upsample_guided(small.astype(np.float32) / 255.0, rgb)


def upsample_guided(small, rgb):
    """A soft mask (0-1, coarser than the photo) at the photo's size, its edges drawn to the photo's own by a
    guided filter on the luminance, worked out on a reduced copy for large photos: 0-1."""
    height, width = rgb.shape[:2]
    matte = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    guide = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    factor = max(height, width) / float(max(small.shape[:2]))
    scale = max(1, int(round(min(height, width) / 1000.0)))
    radius = max(1, int(round(max(2.0, 4.0 * factor) / scale)))
    if scale > 1:
        small_size = (max(1, width // scale), max(1, height // scale))
        g = cv2.resize(guide, small_size, interpolation=cv2.INTER_AREA)
        p = cv2.resize(matte, small_size, interpolation=cv2.INTER_AREA)
    else:
        g, p = guide, matte
    size = (2 * radius + 1, 2 * radius + 1)
    mean_g, mean_p = cv2.boxFilter(g, -1, size), cv2.boxFilter(p, -1, size)
    a = (cv2.boxFilter(g * p, -1, size) - mean_g * mean_p) / (cv2.boxFilter(g * g, -1, size) - mean_g * mean_g + 1e-3)
    b = mean_p - a * mean_g
    mean_a, mean_b = cv2.boxFilter(a, -1, size), cv2.boxFilter(b, -1, size)
    if scale > 1:
        mean_a = cv2.resize(mean_a, (width, height), interpolation=cv2.INTER_LINEAR)
        mean_b = cv2.resize(mean_b, (width, height), interpolation=cv2.INTER_LINEAR)
    return np.clip(mean_a * guide + mean_b, 0, 1)


# 人像解析 (Hair, skin and clothes from Google's selfie multiclass segmentation model: MediaPipe, Apache 2.0)
# Optional: it needs the `ai-edge-litert` package (the TFLite runtime alone, without MediaPipe and its OpenCV 5),
# and fetches the 16 MB model once into the cache folder. Without either, hair is told from skin by texture.
PARSING_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/"
                     "float32/latest/selfie_multiclass_256x256.tflite")
PARSING_MODEL_SHA256 = "c6748b1253a99067ef71f7e26ca71096cd449baefa8f101900ea23016507e0e0"
PARSING_CLASSES = ("background", "hair", "body_skin", "face_skin", "clothes", "others")
CACHE_FOLDER = os.path.expanduser("~/Library/Caches/compositor-retouch")


def parsing_model():
    """A TFLite interpreter with the segmentation model loaded, or None (with a warning saying why)."""
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:
        warn("hair is told from skin by texture; for a sharper split (bangs, strands, skin-colored clothes) install the "
             "optional TFLite runtime: python3 -m pip install ai-edge-litert", once=True)
        return None
    path = os.path.join(CACHE_FOLDER, "selfie_multiclass_256x256.tflite")

    def intact(file=None):
        with open(file or path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == PARSING_MODEL_SHA256
    bundled = os.environ.get("RETOUCH_PARSING_MODEL")  # an app that ships the model names it here
    if bundled and os.path.isfile(bundled) and intact(bundled):
        path = bundled
    try:
        if not (os.path.isfile(path) and intact()):
            import urllib.request
            say("[OK] Downloading the hair and skin segmentation model (16 MB, first run only)")
            os.makedirs(CACHE_FOLDER, exist_ok=True)
            temporary = f"{path}.{os.getpid()}.tmp"
            with urllib.request.urlopen(PARSING_MODEL_URL, timeout=60) as response, open(temporary, "wb") as f:
                shutil.copyfileobj(response, f)
            os.replace(temporary, path)
            if not intact():
                os.remove(path)
                raise OSError("the downloaded model doesn't match its checksum")
        interpreter = Interpreter(model_path=path, num_threads=4)
        interpreter.allocate_tensors()
        return interpreter
    except Exception as error:  # a model problem must never stop the build: hair is then told by texture
        warn(f"the hair and skin segmentation model can't be used ({error}); hair is told from skin by texture", once=True)
        return None


def parse_people(rgb, faces, interpreter):
    """Hair and clothes around each face, from the segmentation model: 8-bit probability maps at the photo's size.
    The model sees a crop about 2.8 face widths across, so a face fills its 256x256 input instead of a few pixels
    of it; away from every face both maps are 0."""
    height, width = rgb.shape[:2]
    maps = {name: np.zeros((height, width), np.uint8) for name in ("hair", "clothes")}
    source, target = interpreter.get_input_details()[0]["index"], interpreter.get_output_details()[0]["index"]
    for x, y, w, h in faces:
        x0, y0 = max(0, int(x - 0.9 * w)), max(0, int(y - 0.9 * h))
        x1, y1 = min(width, int(x + 1.9 * w)), min(height, int(y + 2.2 * h))
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        crop = rgb[y0:y1, x0:x1]
        tensor = cv2.resize(crop, (256, 256), interpolation=cv2.INTER_AREA).astype(np.float32) / 127.5 - 1.0
        interpreter.set_tensor(source, tensor[None])
        interpreter.invoke()
        logits = interpreter.get_tensor(target)[0]
        odds = np.exp(logits - logits.max(axis=-1, keepdims=True))
        probability = odds / odds.sum(axis=-1, keepdims=True)
        for name in maps:
            fine = upsample_guided(np.ascontiguousarray(probability[..., PARSING_CLASSES.index(name)]), crop)
            maps[name][y0:y1, x0:x1] = np.maximum(maps[name][y0:y1, x0:x1], to8(fine))
    return maps


def smooth_skin(rgb, engine, detail_sigma, radius):
    """Evens out blotches and uneven tone while keeping pores: only the band coarser than `detail_sigma` comes
    from the smoothed copy; everything finer is the photo's own. `radius` is the filter's reach. 8-bit RGB or
    floats 0-1 in, the same out."""
    deep = rgb.dtype != np.uint8
    photo = np.ascontiguousarray(rgb, np.float32) if deep else rgb.astype(np.float32) / 255.0
    if engine == "guided":
        smoothed = guided_filter(photo, radius=max(2, int(round(radius))), eps=0.02)
    else:
        diameter = max(5, int(round(radius / 3.0)))
        smoothed = cv2.bilateralFilter(to8(photo) if deep else rgb, diameter, 75, 75).astype(np.float32) / 255.0
    coarse = cv2.GaussianBlur(smoothed, (0, 0), detail_sigma)
    fine = photo - cv2.GaussianBlur(photo, (0, 0), detail_sigma)
    result = np.clip(coarse + fine, 0, 1)
    return result if deep else to8(result)


# The `form` engine (the default). Tuned on 6 NASA portraits with painted spots, creases and blotches, then checked by
# eye on real acne, acne marks and freckles (dark, medium and light skin). Against `guided` at full strength: 59% of
# painted dark spots gone (51%), 77% of the real smile lines' and under-eye shape kept (68%), 22% less change on the
# skin next to eyes, nostrils and lips, 23% less color shift there, the same pore texture.
FORM_EPS = 200.0      # the guided filter's eps in L* units squared: local variation above ~14 L* counts as an edge
FORM_GUARD = 0.5      # how much less it smooths next to eyes, nostrils and lips, whose shadows shape the face
SPOT_LIMIT = 40.0     # a round dip deeper than this (L*, half of it at full strength) is a feature, not a spot
# --texture: where the photo's own detail ends and smoothing begins, as a fraction 1/n of the face width (never under
# 1.5 px). At 88%, against DR5's inverted high pass (High Pass fw/60, Vivid Light, 100%; it keeps 88% of the pore
# texture and takes out 56% of painted acne marks), with the cutoff above the floor: natural keeps 93% of the texture
# and takes out 53%, soft 85% and 60%, smooth 78% and 64%, all with about two thirds of DR5's change next to the
# features. On faces under ~400 px wide the floor makes soft and smooth nearly the same.
TEXTURES = {"natural": (150.0, "保留毛孔"), "soft": (200.0, "质感柔和"), "smooth": (260.0, "质感光滑")}


def masked_guided(p, guide, weight, radius, eps):
    """He's guided filter of `p` (HxW or HxWxC) steered by `guide` (HxW), with every local mean taken over the skin
    only (`weight`, 0-1): lashes, nostrils, lips and hair next to the skin no longer pull it toward their own tone,
    the dark halo a plain guided filter leaves under the lower lid and around the nostrils. Worked out on a copy
    reduced so the box stays about 25 px across (He & Sun's fast guided filter: the linear coefficients are smooth,
    so they upsample cleanly)."""
    height, width = guide.shape
    scale = max(1, int(round(radius / 12.0)))
    if scale > 1:
        size = (max(1, width // scale), max(1, height // scale))
        g, q, w = (cv2.resize(x, size, interpolation=cv2.INTER_AREA) for x in (guide, p, weight))
        radius = max(1, int(round(radius / scale)))
    else:
        g, q, w = guide, p, weight
    box = (2 * radius + 1, 2 * radius + 1)
    support = np.maximum(cv2.boxFilter(w, -1, box), 1e-4)
    channels = q.ndim == 3

    def mean(x):
        if x.ndim == 3:
            return cv2.boxFilter(x * w[..., None], -1, box) / support[..., None]
        return cv2.boxFilter(x * w, -1, box) / support

    mean_g = mean(g)
    variance = mean(g * g) - mean_g * mean_g
    if channels:
        g, mean_g, variance = g[..., None], mean_g[..., None], variance[..., None]
    mean_q = mean(q)
    a = (mean(g * q) - mean_g * mean_q) / (variance + eps)
    b = mean_q - a * mean_g
    mean_a, mean_b = mean(a), mean(b)
    if scale > 1:
        mean_a = cv2.resize(mean_a, (width, height), interpolation=cv2.INTER_LINEAR)
        mean_b = cv2.resize(mean_b, (width, height), interpolation=cv2.INTER_LINEAR)
    return mean_a * (guide[..., None] if channels else guide) + mean_b


def roundness(lightness, weight, sigma):
    """0-1 at scale `sigma`: 1 where the lightness curves the same way in every direction (a spot), 0 where it curves
    across itself only (a crease, a contour, the edge of a feature) or both ways (a saddle). The ratio of the two
    curvatures, as `_blobness` measures at a single point, softened over half the scale so a spot's rim goes with it."""
    smooth = masked_blur(lightness, weight, sigma)
    dxx = cv2.Sobel(smooth, cv2.CV_32F, 2, 0, ksize=3)
    dyy = cv2.Sobel(smooth, cv2.CV_32F, 0, 2, ksize=3)
    dxy = cv2.Sobel(smooth, cv2.CV_32F, 1, 1, ksize=3)
    spread = np.sqrt(((dxx - dyy) / 2) ** 2 + dxy ** 2)
    low, high = np.abs((dxx + dyy) / 2 - spread), np.abs((dxx + dyy) / 2 + spread)
    ratio = np.where(dxx * dyy - dxy * dxy > 0, np.minimum(low, high) / (np.maximum(low, high) + 1e-6), 0.0)
    t = np.clip((ratio - 0.15) / 0.35, 0, 1)
    return cv2.GaussianBlur((t * t * (3 - 2 * t)).astype(np.float32), (0, 0), 0.5 * sigma)


def smooth_skin_form(rgb, skin, faces, face_width, detail_sigma=None, reach=None):
    """Smoothing that keeps the face's form (8-bit RGB or floats 0-1 in, the same out):
    - the coarse unevenness a guided filter finds, as `guided` takes out, but in Lab (lightness and color apart) and
      with skin-only statistics (see masked_guided), so no dark halo from lashes, nostrils, lips or hair;
    - eased next to the eyes, nostrils and lips, whose shadows give the face its shape;
    - round dark spots (acne marks, small brown spots) faded whatever their contrast, which the guided filter keeps
      as edges; lines, contours and highlights are left alone, and so are the ears (only the face's oval counts).
    Detail finer than `detail_sigma` (pores) is the photo's own. Worked out on the bounding box of `reach` (8-bit;
    the skin by default, see Project.skin_reach for a layer brushed on by hand), whose values weigh the statistics;
    where it is zero the result is the photo itself, so nothing strange shows if a mask reveals it there."""
    deep = rgb.dtype != np.uint8
    photo = np.ascontiguousarray(rgb, np.float32) if deep else rgb.astype(np.float32) / 255.0
    out = photo.copy()
    where = skin if reach is None else reach
    if where.max() == 0:
        return out if deep else to8(out)
    box = crop_box(where, max(8, int(face_width / 6)))
    weight = where[box].astype(np.float32) / 255.0
    crop_faces = [shifted(face, -box[1].start, -box[0].start) for face in faces]
    lab = cv2.cvtColor(np.ascontiguousarray(photo[box]), cv2.COLOR_RGB2Lab)
    lightness, color = np.ascontiguousarray(lab[..., 0]), np.ascontiguousarray(lab[..., 1:])
    sigma = detail_sigma or max(1.5, face_width / 150.0)
    radius = max(4, int(round(face_width / 30.0)))
    near = cv2.GaussianBlur(feature_mask(weight.shape, crop_faces).astype(np.float32) / 255.0, (0, 0),
                            max(1.0, face_width / 60.0))
    ease = 1.0 - FORM_GUARD * near
    # The coarse unevenness, lightness and color, as the guided filter sees it on the skin alone.
    smooth_l = masked_guided(lightness, lightness, weight, radius, FORM_EPS)
    smooth_c = masked_guided(color, lightness, weight, radius, FORM_EPS)
    # The correction is blurred over the skin alone too (`inner`: the mask's solid part): beyond the skin the guided
    # filter predicts skin, so the residual there is hair or a collar against skin, tens of L*, and a plain blur
    # smeared it a few pixels into the skin as a light or dark fringe along the hairline, jaw and collar.
    inner = np.clip((weight - 0.5) * 4.0, 0, 1)
    lightness = lightness - masked_blur(lightness - smooth_l, inner, sigma) * ease
    color = color - masked_blur(color - smooth_c, inner, sigma) * ease[..., None]
    if crop_faces:  # round dark spots, between pore size and 1/25 of the face, on the face's oval
        solid = np.clip((weight - 0.55) / 0.4, 0, 1)
        solid = solid * solid * (3 - 2 * solid)  # half-covered hair or earrings would leak their dark into the blurs
        oval = _ellipse(weight.shape, crop_faces, 0.50, 0.55, 0.52, 0.62).astype(np.float32) / 255.0
        oval_where = cv2.GaussianBlur(oval, (0, 0), max(1.0, face_width / 20.0)) * ease
        edges = np.geomspace(max(1.0, face_width / 150.0), max(2.0, face_width / 25.0), 4)
        levels = [masked_blur(lightness, solid, s) for s in edges]
        dips, gate = np.zeros_like(lightness), np.zeros_like(lightness)
        for i in range(3):
            band = levels[i] - levels[i + 1]
            spot = roundness(lightness, solid, math.sqrt(edges[i] * edges[i + 1])) * (band < 0) \
                * np.clip((SPOT_LIMIT - np.abs(band)) / (SPOT_LIMIT / 2), 0, 1)
            dips += spot * band
            gate = np.maximum(gate, spot)
        lightness = lightness - dips * oval_where
        color_band = masked_blur(color, solid, edges[0]) - masked_blur(color, solid, edges[-1])
        color = color - color_band * (gate * oval_where)[..., None]  # the spot's color too, toward its surroundings
    smoothed = cv2.cvtColor(np.dstack([lightness, color]).astype(np.float32), cv2.COLOR_Lab2RGB)
    keep = inner[..., None]  # outside the mask's solid part the layer is the photo itself: no fringe to reveal
    out[box] = np.clip(photo[box] * (1.0 - keep) + smoothed * keep, 0, 1)
    return out if deep else to8(out)


class Face(tuple):
    """A frontal face box (x, y, w, h) in image pixels, sized like OpenCV's Haar boxes so the proportions used
    below fit either detector, with Apple Vision's landmarks (`marks`: name -> Nx2 points) when it found them."""

    def __new__(cls, box, marks=None):
        face = super().__new__(cls, tuple(float(v) for v in box))
        face.marks = marks
        return face


def scaled(face, factor):
    """The face on a copy of the image resized by `factor`, landmarks included."""
    marks = getattr(face, "marks", None)
    return Face(tuple(v * factor for v in face), {name: points * factor for name, points in marks.items()} if marks else None)


def shifted(face, dx, dy):
    """The face moved by (dx, dy), landmarks included: for working on a crop of the image."""
    x, y, w, h = face
    marks = getattr(face, "marks", None)
    offset = np.array([dx, dy], np.float32)
    return Face((x + dx, y + dy, w, h), {name: points + offset for name, points in marks.items()} if marks else None)


VISION_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_landmarks.swift")


def vision_tool():
    """The Apple Vision face helper, compiled once into ~/Library/Caches; None where it can't be built. An app that
    ships it prebuilt names it in RETOUCH_VISION_HELPER (no compiler needed then, and no wait on the first run)."""
    prebuilt = os.environ.get("RETOUCH_VISION_HELPER")
    if sys.platform == "darwin" and prebuilt and os.access(prebuilt, os.X_OK):
        return prebuilt
    if sys.platform != "darwin" or not os.path.isfile(VISION_SOURCE):
        return None
    with open(VISION_SOURCE, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:12]
    folder = os.path.expanduser("~/Library/Caches/compositor-retouch")
    binary = os.path.join(folder, f"face_landmarks-{digest}")
    if os.access(binary, os.X_OK):
        return binary
    compiler = shutil.which("swiftc")
    if compiler is None:
        warn("swiftc (Xcode Command Line Tools) not found, so faces are found by OpenCV, without landmarks; "
             "install them with `xcode-select --install`", once=True)
        return None
    temporary = f"{binary}.{os.getpid()}.tmp"
    say("[OK] Building the Apple Vision face helper (first run only)")
    try:  # a Vision problem must never stop the build: anything going wrong here falls back to OpenCV
        os.makedirs(folder, exist_ok=True)
        result = subprocess.run([compiler, "-O", "-swift-version", "5", "-o", temporary, VISION_SOURCE],
                                capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not os.path.isfile(temporary):
            warn(f"could not build the Apple Vision face helper; faces are found by OpenCV: "
                 f"{result.stderr.strip()[-300:]}", once=True)
            return None
        os.replace(temporary, binary)
    except (OSError, subprocess.TimeoutExpired) as error:
        warn(f"could not build the Apple Vision face helper ({error}); faces are found by OpenCV", once=True)
        return None
    return binary


def box_from_landmarks(marks):
    """The Haar-sized box around Vision's landmarks. On a frontal portrait both detectors found, the Haar box was
    2.3 eye-to-eye distances wide, centered between the eyes, with its top 0.375 box widths above them."""
    left, right = marks["leftEye"].mean(axis=0), marks["rightEye"].mean(axis=0)
    width = 2.3 * float(np.hypot(*(right - left)))
    middle = (left + right) / 2
    return middle[0] - width / 2, middle[1] - 0.375 * width, width, width


def vision_faces(rgb):
    """Faces with landmarks from Apple Vision, or None when Vision can't be used."""
    return vision_detect(rgb)[0]


def vision_detect(rgb):
    """Apple Vision's faces with landmarks, and its person segmentation (people white, 8-bit, at Vision's own
    resolution); None for what Vision couldn't provide."""
    binary = vision_tool()
    if binary is None:
        return None, None
    height, width = rgb.shape[:2]
    scale = min(1.0, 2048.0 / max(height, width))  # landmarks don't need more, and it keeps the hand-off quick
    small = rgb if scale >= 1.0 else cv2.resize(rgb, (max(1, round(width * scale)), max(1, round(height * scale))),
                                                 interpolation=cv2.INTER_AREA)
    matte = None
    with tempfile.TemporaryDirectory() as folder:
        path, matte_path = os.path.join(folder, "faces.png"), os.path.join(folder, "people.png")
        Image.fromarray(small).save(path, compress_level=1)
        try:
            result = subprocess.run([binary, path, "--matte", matte_path], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as error:
            warn(f"the Apple Vision face helper failed ({error}); faces are found by OpenCV", once=True)
            return None, None
        if result.returncode == 0 and os.path.isfile(matte_path):
            try:
                with Image.open(matte_path) as source:
                    matte = np.asarray(source.convert("L"))
            except OSError:
                matte = None
    try:
        found = json.loads(result.stdout)["faces"] if result.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        found = None
    if found is None:
        warn(f"the Apple Vision face helper failed; faces are found by OpenCV: {result.stderr.strip()[-300:]}", once=True)
        return None, matte
    faces = []
    for face in found:
        marks = {name: np.array(points, np.float32) / scale for name, points in face.get("landmarks", {}).items()}
        if len(marks.get("leftEye", ())) >= 3 and len(marks.get("rightEye", ())) >= 3:
            faces.append(Face(box_from_landmarks(marks), marks))
        else:  # no eyes to measure: Vision's own box, brought to the Haar box's size and place
            x, y, w, h = (v / scale for v in face["box"])
            faces.append(Face((x + 0.045 * w, y - 0.07 * w, 0.92 * w, 0.92 * w)))
    if faces:  # small detections next to a portrait's face are almost always false positives
        largest = max(face[2] for face in faces)
        faces = [face for face in faces if face[2] >= 0.25 * largest]
    return faces, matte


def detect_faces(rgb, use_vision=True):
    """Faces: from Apple Vision with landmarks on a Mac, otherwise (or when Vision finds none) from OpenCV."""
    return detect(rgb, use_vision)[0]


def detect(rgb, use_vision=True):
    """The faces (see detect_faces) and Apple Vision's person matte at its own resolution, or None without one."""
    faces, matte = vision_detect(rgb) if use_vision else (None, None)
    return (faces or [Face(box) for box in haar_faces(rgb)]), matte


def haar_faces(rgb):
    """Frontal faces as (x, y, w, h) in image pixels; empty when none are found or OpenCV lacks the detector."""
    if not hasattr(cv2, "CascadeClassifier"):
        warn(f"OpenCV {cv2.__version__} has no Haar face detector (OpenCV 5 removed it), so the skin mask is "
             f"color-only. For face locking: python3 -m pip install 'opencv-python-headless<5'", once=True)
        return []
    cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    if cascade.empty():
        warn("OpenCV's frontal-face cascade file is missing; the skin mask is color-only", once=True)
        return []
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Detecting on a ~1024 px copy is many times faster on large photos and finds the same portrait faces.
    scale = min(1.0, 1024.0 / max(height, width))
    if scale < 1.0:
        gray = cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))),
                          interpolation=cv2.INTER_AREA)
    found = cascade.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    faces = [(x / scale, y / scale, w / scale, h / scale) for (x, y, w, h) in found]
    if faces:  # small detections next to a portrait's face are almost always false positives
        largest = max(face[2] for face in faces)
        faces = [face for face in faces if face[2] >= 0.25 * largest]
    return faces


def _ellipse(shape, faces, cx, cy, rx, ry):
    mask = np.zeros(shape, np.uint8)
    for (x, y, w, h) in faces:
        cv2.ellipse(mask, (int(round(x + cx * w)), int(round(y + cy * h))),
                    (max(1, int(round(rx * w))), max(1, int(round(ry * h)))), 0, 0, 360, 255, -1)
    return mask


def texture(rgb, skin, faces):
    """How densely detailed each area is, relative to the cheeks (1 = like the cheeks): skin is smooth at fine
    scale, while hair, brows, lashes and stubble are full of strands. It is the median of the fine detail over a
    patch, so a single edge (jawline, crease, collar) that lights up only a thin line doesn't count; a plain
    average did, and took 19% of the neck for hair on a real portrait. None when the cheeks can't be measured."""
    face_width = max(face[2] for face in faces)
    height, width = rgb.shape[:2]
    y = luma(rgb.astype(np.float32) / 255.0)
    magnitude = np.abs(y - cv2.GaussianBlur(y, (0, 0), max(1.0, face_width / 250.0)))
    factor = max(1, int(face_width // 200))  # the patch is wide, so work on a reduced copy
    if factor > 1:
        magnitude = cv2.resize(magnitude, (max(1, width // factor), max(1, height // factor)),
                               interpolation=cv2.INTER_AREA)
    detail8 = np.clip(magnitude * 2040.0, 0, 255).astype(np.uint8)
    density = cv2.medianBlur(detail8, min(255, odd(max(5, face_width / 12.0 / factor))))
    density = cv2.resize(density, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    cheeks = (_ellipse(skin.shape, faces, 0.50, 0.62, 0.32, 0.12) > 0) & (skin > 200) \
        & (feature_mask(skin.shape, faces) == 0)
    if cheeks.sum() < 50:
        return None
    return density / max(1.0, float(np.median(density[cheeks])))


def lower_face(shape, faces):
    """Below the eyes and inside the jaw line (Vision's face contour), or the lower part of the face box without
    landmarks: where the only hair is a beard or moustache."""
    mask = np.zeros(shape, np.uint8)
    for face in faces:
        marks = getattr(face, "marks", None)
        contour = marks.get("faceContour") if marks else None
        if contour is not None and len(contour) >= 5:
            eyes = max(marks["leftEye"][:, 1].max(), marks["rightEye"][:, 1].max())
            _fill(mask, np.vstack([contour, [[contour[-1][0], eyes], [contour[0][0], eyes]]]))
            mask[:max(0, int(eyes))] = 0  # (a tilted head is cut at its lower eye)
        else:
            x, y, w, h = face
            mask[max(0, int(y + 0.45 * h)):max(0, int(y + h)), max(0, int(x)):max(0, int(x + w))] = 255
    return mask > 0


def hair_mask(rgb, skin, faces, color_distance=None):
    """Hair, brows, lashes and stubble inside the skin-colored area: more than 2.4x the cheeks' detail density
    (on a real portrait that catches ~95% of skin-colored hair and leaves cheeks, chin, neck and jaw clear).
    With `color_distance` (see skin_color), below the eyes it also has to be unlike the skin's color: lined or
    textured skin is as detailed as hair, and beards are never skin-colored (light bangs above can be).
    Faces under 250 px wide don't have reliable fine detail, so there only the area above the mouth is checked."""
    face_width = max(face[2] for face in faces)
    relative = texture(rgb, skin, faces)
    if relative is None:
        return np.zeros(skin.shape, np.uint8)
    hair = (relative > 2.4) & (skin > 0)
    if color_distance is not None:
        hair &= ~lower_face(skin.shape, faces) | (color_distance > 3.0)
    hair = hair.astype(np.uint8) * 255
    if face_width < 250:
        for (x, y, w, h) in faces:
            hair[int(y + 0.75 * h):, max(0, int(x - 0.3 * w)):int(x + 1.3 * w)] = 0
    small = odd(max(3, face_width / 100.0))
    hair = cv2.morphologyEx(hair, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (small, small)))
    return cv2.dilate(hair, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (small, small)))


def reference_zones(shape, faces):
    """Cheek apples and mid forehead: flat, front-lit skin to learn a face's skin color from."""
    zones = np.zeros(shape, np.uint8)
    for face in faces:
        marks = getattr(face, "marks", None)
        if marks:
            eye = eye_width(marks)
            across, down, tilt = _face_axes(marks)
            between = (marks["leftEye"].mean(axis=0) + marks["rightEye"].mean(axis=0)) / 2
            for side in ("leftEye", "rightEye"):
                middle = marks[side].mean(axis=0)
                outward = across * np.sign((middle - between) @ across)
                _ellipse_at(zones, middle + down * 1.1 * eye + outward * 0.15 * eye, (0.5 * eye, 0.35 * eye), tilt)
            brows = np.vstack([marks[name] for name in ("leftEyebrow", "rightEyebrow") if name in marks]
                              or [marks["leftEye"], marks["rightEye"]])
            top = brows[np.argmin(brows @ down)]
            _ellipse_at(zones, brows.mean(axis=0) + down * ((top - brows.mean(axis=0)) @ down - 0.6 * eye),
                        (1.0 * eye, 0.3 * eye), tilt)
        else:
            x, y, w, h = face
            for cx in (0.30, 0.70):
                cv2.ellipse(zones, (int(x + cx * w), int(y + 0.62 * h)), (max(1, int(0.11 * w)), max(1, int(0.08 * w))),
                            0, 0, 360, 255, -1)
            cv2.ellipse(zones, (int(x + 0.5 * w), int(y + 0.2 * h)), (max(1, int(0.2 * w)), max(1, int(0.07 * w))),
                        0, 0, 360, 255, -1)
    return zones > 0


def skin_color(rgb, faces, skin_tone, people=None):
    """How far each pixel's color is from the nearest face's own skin (robust standard deviations of the skin
    sampled on its cheeks and forehead), and how bright it is next to that skin. Color is the chromaticity of
    linear light (r, g over r + g + b), which stays put when the same skin is in shadow, so a dark cheek on the
    unlit side of a face still matches. Skin varies mostly in how saturated it is, so the spread runs towards
    gray: anything less than half as saturated as the face's skin (a gray backdrop, a white collar) is out."""
    height, width = rgb.shape[:2]
    factor = max(1, int(max(face[2] for face in faces) // 400))  # color doesn't need more than this (and it's quick)
    if factor > 1:
        size = (max(1, width // factor), max(1, height // factor))
        distance, brightness = skin_color(cv2.resize(rgb, size, interpolation=cv2.INTER_AREA),
                                          [scaled(face, 1.0 / factor) for face in faces],
                                          cv2.resize(skin_tone, size, interpolation=cv2.INTER_NEAREST),
                                          None if people is None else cv2.resize(people, size, interpolation=cv2.INTER_AREA))
        distance = np.where(np.isfinite(distance), distance, 1e6).astype(np.float32)
        return (cv2.resize(distance, (width, height), interpolation=cv2.INTER_LINEAR),
                cv2.resize(brightness, (width, height), interpolation=cv2.INTER_LINEAR))
    face_width = max(face[2] for face in faces)
    linear = srgb_to_linear(cv2.GaussianBlur(rgb.astype(np.float32) / 255.0, (0, 0), max(0.8, face_width / 300.0)))
    total = linear.sum(axis=-1) + 1e-4
    chroma = np.dstack([linear[..., 0] / total, linear[..., 1] / total])
    saturation = np.hypot(chroma[..., 0] - 1 / 3, chroma[..., 1] - 1 / 3)
    light = linear @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    sample = reference_zones(rgb.shape[:2], faces) & (skin_tone > 0) & (feature_mask(rgb.shape[:2], faces) == 0)
    if people is not None:
        sample &= people > 0.5
    distance = np.full(rgb.shape[:2], np.inf, np.float32)
    brightness = np.zeros(rgb.shape[:2], np.float32)
    for x, y, w, h in faces:
        near = np.zeros(rgb.shape[:2], bool)
        near[max(0, int(y - h)):max(0, int(y + 2.5 * h)), max(0, int(x - w)):max(0, int(x + 2 * w))] = True
        values = chroma[sample & near]
        if len(values) < 30:
            continue
        center = np.median(values, axis=0)
        spread = 1.4826 * np.median(np.abs(values - center), axis=0) + 1e-6
        values = values[(np.abs(values - center) < 3 * spread).all(axis=1)]
        inverse = np.linalg.inv(np.cov(values.T) + np.eye(2) * 1e-6)
        offset = chroma - center
        own = np.sqrt(np.einsum("...i,ij,...j->...", offset, inverse, offset))
        own[saturation < 0.5 * float(np.hypot(center[0] - 1 / 3, center[1] - 1 / 3))] = np.inf
        closer = near & (own < distance)
        distance[closer] = own[closer]
        brightness[closer] = (light / max(1e-6, float(np.median(light[sample & near]))))[closer]
    return distance, brightness


def skin_mask(rgb, faces, people=None, parts=None):
    """Skin mask, feathered: skin-toned (YCrCb), or matching the face's own skin color however much in shadow (see
    skin_color), minus dark features (brows, lashes, pupils, nostrils), confined to the face and neck when faces
    were found and to the people in the photo (`people`, Vision's person matte, 0-1) when there is a matte,
    without hair: from the segmentation model's hair and clothes (`parts`, see parse_people) when there are those,
    by texture otherwise (beards always by texture and color). Returns the mask, a short label for the layer name
    and the hair that was taken out (None without a face)."""
    height, width = rgb.shape[:2]
    tone = cv2.inRange(cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb), (0, 133, 77), (255, 173, 127))
    skin = tone.copy()
    skin[cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) < 45] = 0
    hair = None
    if faces:
        distance, brightness = skin_color(rgb, faces, tone, people)
        # Within 3.5 deviations of the face's own color and not darker than a tenth of it: shadowed skin too.
        skin[(distance < 3.5) & (brightness > 0.1)] = 255
        # An ellipse anchored low on the face, reaching down over chin and neck.
        skin = cv2.bitwise_and(skin, _ellipse(skin.shape, faces, 0.50, 0.65, 0.68, 0.90))
        if people is not None:
            skin[people < 0.5] = 0
        hair = hair_mask(rgb, skin, faces, distance)
        if parts is not None:
            # The model sees hair as hair however skin-colored and finds no hair in lined skin, but may miss a
            # beard: texture (with color) keeps its say below the eyes only.
            hair[~lower_face(skin.shape, faces)] = 0
            hair = np.maximum(hair, (parts["hair"] > 127).astype(np.uint8) * 255)
            skin[parts["clothes"] > 127] = 0
        skin[hair > 0] = 0
        landmarked = landmark_faces(faces)
        if landmarked:  # eyes, brows and lips aren't skin: their outlines from Vision
            skin[feature_mask(skin.shape, landmarked, ("eyes", "mouth")) > 0] = 0
        label = f"面部锁定 ×{len(faces)}"
    else:
        warn("no face found: the skin mask follows skin color across the whole frame and may include warm "
             "clothing or wood. Check the mask in Compositor (Option-click its thumbnail).")
        label = "仅肤色"
    kernel = odd(max(3, round(min(height, width) / 400.0)))
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))
    feather = odd(max(9, round(min(height, width) / 180.0)))
    return cv2.GaussianBlur(skin, (feather, feather), 0), label, hair


# Where eyes, nostrils and mouth sit in a frontal face box (center x, center y, radius x, radius y, as fractions).
FEATURES = {
    "eyes": ((0.30, 0.40, 0.17, 0.11), (0.70, 0.40, 0.17, 0.11)),
    "nose": ((0.50, 0.65, 0.27, 0.11),),  # wide enough for the creases beside the nostrils
    "mouth": ((0.50, 0.81, 0.27, 0.12),),
}


def inner_face(shape, faces):
    """Cheeks, mid forehead and chin: the part of a frontal face box hair rarely covers (the temples and the
    corners of the hairline often hold skin-colored hair that neither color nor texture gives away)."""
    return _ellipse(shape, faces, 0.50, 0.55, 0.42, 0.55)


def feature_mask(shape, faces, parts=("eyes", "nose", "mouth")):
    """Facial features with a margin: from Vision's landmarks when there are any (eyes with their lids and brows,
    the nose, the lips), otherwise ellipses placed in the face box. Healing keeps away from all of them (lash lines,
    nostrils and the gaps between teeth look like dark spots), and the smoothing eases off next to them."""
    mask = np.zeros(shape, np.uint8)
    for face in faces:
        marks = getattr(face, "marks", None)
        if not marks:
            x, y, w, h = face
            for part in parts:
                for (cx, cy, rx, ry) in FEATURES[part]:
                    cv2.ellipse(mask, (int(round(x + cx * w)), int(round(y + cy * h))),
                                (int(round(rx * w)), int(round(ry * h))), 0, 0, 360, 255, -1)
            continue
        eye = eye_width(marks)
        part_mask = np.zeros(shape, np.uint8)
        if "eyes" in parts:
            for name in ("leftEye", "rightEye"):
                _fill(part_mask, marks[name])
        if "nose" in parts and "nose" in marks:
            _fill(part_mask, cv2.convexHull(marks["nose"]).reshape(-1, 2))
        if "mouth" in parts and "outerLips" in marks:
            _fill(part_mask, marks["outerLips"])
        # A margin for lids, lashes, nostril creases and the lip line ...
        grow = odd(max(3, 0.5 * eye))
        mask |= cv2.dilate(part_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow)))
        if "eyes" in parts:  # ... but none around the brows: the skin between and above them is a T-zone
            for name in ("leftEyebrow", "rightEyebrow"):
                if name in marks:
                    _line(mask, marks[name], 0.35 * eye)
    return mask


# ---- shapes from Vision's landmarks: masks that come painted in, for layers that would otherwise start black ----

def _fill(mask, points, value=255):
    cv2.fillPoly(mask, [np.round(points).astype(np.int32).reshape(-1, 1, 2)], value)


def _line(mask, points, thickness, value=255):
    cv2.polylines(mask, [np.round(points).astype(np.int32).reshape(-1, 1, 2)], False, value,
                  max(1, int(round(thickness))))


def eye_width(marks):
    return float(np.mean([np.ptp(marks[name][:, 0]) for name in ("leftEye", "rightEye")]))


def _feathered(mask, sigma, strength=1.0):
    return np.clip(cv2.GaussianBlur(mask, (0, 0), max(1.0, sigma)).astype(np.float32) * strength, 0, 255).astype(np.uint8)


def landmark_faces(faces):
    return [face for face in faces if getattr(face, "marks", None)]


def _iris(shape, marks, side, grow=1.0):
    eye = marks[f"{side}Eye"]
    pupil = marks.get(f"{side}Pupil")
    center = pupil[0] if pupil is not None and len(pupil) else eye.mean(axis=0)
    circle = np.zeros(shape, np.uint8)
    # An iris is about 0.42 of the eye's corner-to-corner width across.
    cv2.circle(circle, (int(round(center[0])), int(round(center[1]))),
               max(1, int(round(0.21 * np.ptp(eye[:, 0]) * grow))), 255, -1)
    return circle


def iris_mask(shape, faces, strength=0.85):
    """The irises, inside the eye openings: where eye brilliance goes."""
    mask = np.zeros(shape, np.uint8)
    widths = []
    for face in landmark_faces(faces):
        for side in ("left", "right"):
            opening = np.zeros(shape, np.uint8)
            _fill(opening, face.marks[f"{side}Eye"])
            mask |= _iris(shape, face.marks, side) & opening
        widths.append(eye_width(face.marks))
    return _feathered(mask, np.median(widths) / 25 if widths else 1, strength)


def _face_axes(marks):
    """The face's own directions in the image: across (from the eye Vision calls left to the one it calls right,
    labels that hold however far the head is turned; sorting the eyes by x instead pointed "down" at the forehead
    on a face turned past 90°), down (towards the mouth), and how far the head is tilted, in degrees, so shapes
    placed by the landmarks turn with it."""
    left, right = marks["leftEye"].mean(axis=0), marks["rightEye"].mean(axis=0)
    across = (right - left) / max(1e-6, float(np.hypot(*(right - left))))
    down = np.array([-across[1], across[0]], np.float32)
    # Whatever the labels, the mouth (else the nose) lies down from the eyes.
    anchor = next((marks[name] for name in ("outerLips", "innerLips", "nose", "medianLine") if name in marks), None)
    if anchor is not None and float((np.asarray(anchor, np.float32).mean(axis=0) - (left + right) / 2) @ down) < 0:
        across, down = -across, -down
    return across, down, math.degrees(math.atan2(across[1], across[0]))


def _ellipse_at(mask, center, axes, angle):
    cv2.ellipse(mask, (int(round(center[0])), int(round(center[1]))),
                (max(1, int(round(axes[0]))), max(1, int(round(axes[1])))), angle, 0, 360, 255, -1)


def sculpt_mask(shape, faces, strength=0.5):
    """Where a retoucher builds up 3D form: the nose bridge, the tops of the cheekbones, the brow bones and the
    chin, softly, at half strength, for the user to build on or take back."""
    mask = np.zeros(shape, np.uint8)
    widths = []
    for face in landmark_faces(faces):
        marks, width = face.marks, eye_width(face.marks)
        across, down, tilt = _face_axes(marks)
        middle = (marks["leftEye"].mean(axis=0) + marks["rightEye"].mean(axis=0)) / 2
        if "noseCrest" in marks:
            _line(mask, marks["noseCrest"], 0.22 * width)
        for side in ("left", "right"):
            eye = marks[f"{side}Eye"]
            outward = 1.0 if float((eye.mean(axis=0) - middle) @ across) > 0 else -1.0
            outer = eye[np.argmax(((eye - middle) @ across) * outward)]
            # The cheekbone line rises towards the ear.
            center = outer + across * outward * 0.1 * width + down * (float((eye.mean(axis=0) - outer) @ down) + 0.75 * width)
            _ellipse_at(mask, center, (0.6 * width, 0.25 * width), tilt - 25 * outward)
            brow = marks.get(f"{side}Eyebrow")
            if brow is not None:
                _line(mask, brow - down * 0.25 * width, 0.25 * width)
        if "faceContour" in marks:
            contour = marks["faceContour"]
            chin = contour[np.argmax((contour - middle) @ down)]
            _ellipse_at(mask, chin - down * 0.35 * width, (0.45 * width, 0.2 * width), tilt)
        widths.append(width)
    return _feathered(mask, np.median(widths) * 0.2 if widths else 1, strength)


# ==========================================
# 自动修饰 (Automatic skin fixes)
# ==========================================

def robust_std(values):
    if values.size == 0:
        return 1.0
    values = values[::max(1, values.size // 200_000)]
    median = np.median(values)
    return max(1e-6, 1.4826 * float(np.median(np.abs(values - median))))


def find_blemishes(rgb, skin, guard, relative_texture, face_width, strength, joint=False):
    """Small dark or red spots on clear skin, as [(x, y, radius, score)], plus the clear-skin area used to find them.
    A spot stands out from the skin around it by several times the skin's usual variation, at a size between
    pore-plus and a small mole; long thin shapes (wrinkles, hairs) are left alone. Anything with more fine detail
    than 1.8x the cheeks (stray hair, bangs, brows) is not searched: hair strands look like dark spots. With `joint`,
    a spot somewhat darker and somewhat redder at once counts too (the sum of the two measures): that is what most
    acne marks are, and it finds about three times as many on acne-prone skin, but also freckles."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2Lab)
    lightness, redness = lab[..., 0], lab[..., 1]
    weight = skin.astype(np.float32) / 255.0
    core = ((skin > 200) & (guard == 0)).astype(np.uint8)
    if relative_texture is not None:
        core[relative_texture > 1.8] = 0
    size = odd(max(3, face_width / 60.0))
    core = cv2.erode(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)))
    inside = core > 0
    if inside.sum() < 100:
        return [], core
    smallest = max(1.5, face_width / 250.0)
    largest = max(3 * smallest, face_width / 40.0)
    score = np.zeros(skin.shape, np.float32)
    for sigma in np.geomspace(smallest / 1.5, largest / 1.5, 3):
        dark = masked_blur(lightness, weight, 4 * sigma) - gaussian(lightness, sigma)
        red = gaussian(redness, sigma) - masked_blur(redness, weight, 4 * sigma)
        z_dark, z_red = dark / robust_std(dark[inside]), red / robust_std(red[inside])
        for contrast, z, floor in ((dark, z_dark, 2.5), (red, z_red, 3.0)):  # at least 2.5 L* darker, or 3 a* redder
            score = np.maximum(score, np.where(contrast > floor, z, 0))
        if joint:
            both = np.maximum(z_dark, 0) + np.maximum(z_red, 0)
            score = np.maximum(score, np.where((dark > 1.5) & (red > 1.0), both, 0))
    spots = ((score > 5.0 / strength) & inside).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(spots, connectivity=8)
    found = []
    for i in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[i])
        radius = math.sqrt(area / math.pi)
        if not smallest * 0.6 <= radius <= largest * 1.3:
            continue
        if max(w, h) > 2.5 * min(w, h) or area < 0.35 * w * h:
            continue
        cx, cy = float(centroids[i][0]), float(centroids[i][1])
        sigma = max(1.0, radius / 1.2)
        if max(_blobness(lightness, cx, cy, sigma, 1), _blobness(redness, cx, cy, sigma, -1)) < 0.35:
            continue  # part of a crease or a wrinkle, not a spot
        peak = float(score[y:y + h, x:x + w][labels[y:y + h, x:x + w] == i].max())
        found.append((cx, cy, radius, peak))
    found.sort(key=lambda spot: -spot[3])  # too many: keep the strongest
    return found[:MAX_SPOTS], core


def _blobness(channel, cx, cy, sigma, sign):
    """How round the dip (sign=+1, a dark spot in L*) or peak (sign=-1, a red spot in a*) at (cx, cy) is, 0-1:
    the ratio of the two curvatures there. A spot curves the same way in every direction; a crease or a wrinkle
    curves only across itself, so its ratio is near 0."""
    reach = int(4 * sigma) + 2
    x, y = int(round(cx)), int(round(cy))
    if y - reach < 0 or x - reach < 0 or y + reach >= channel.shape[0] or x + reach >= channel.shape[1]:
        return 0.0
    patch = cv2.GaussianBlur(channel[y - reach:y + reach + 1, x - reach:x + reach + 1], (0, 0), sigma) * sign
    c = reach
    dxx = patch[c, c + 1] - 2 * patch[c, c] + patch[c, c - 1]
    dyy = patch[c + 1, c] - 2 * patch[c, c] + patch[c - 1, c]
    dxy = (patch[c + 1, c + 1] - patch[c + 1, c - 1] - patch[c - 1, c + 1] + patch[c - 1, c - 1]) / 4
    spread = math.sqrt(((dxx - dyy) / 2) ** 2 + dxy ** 2)
    low, high = (dxx + dyy) / 2 - spread, (dxx + dyy) / 2 + spread
    return float(low / high) if low > 0 and high > 0 else 0.0


ACNE_SPOTS = 10  # spots per face, found the strict way, from which the skin counts as acne-prone (clear skin: 0-5)


def blemish_guard(shape, faces, face_width, skin, wide):
    """Where spots are not searched (255): the features with a margin, and either everything outside the inner face
    or, `wide` (hair is told from skin by the segmentation model, so the forehead, temples and jaw are safe to
    search), outside the face's oval, a whole eye's width around each eye (corners, lid creases and the hollow by
    the nose look like spots) and 1/40 of the face from the skin's edge (hairline, jaw)."""
    guard = feature_mask(shape, faces)
    if not wide:
        return np.maximum(guard, 255 - inner_face(shape, faces))
    for face in landmark_faces(faces):
        eye = eye_width(face.marks)
        for name in ("leftEye", "rightEye"):  # mostly below and beside the eye: the brow side is a forehead
            center = face.marks[name].mean(axis=0)
            cv2.ellipse(guard, (int(round(center[0])), int(round(center[1] + 0.1 * eye))),
                        (max(1, int(round(0.9 * eye))), max(1, int(round(0.45 * eye)))), 0, 0, 360, 255, -1)
    guard = np.maximum(guard, 255 - _ellipse(shape, faces, 0.50, 0.50, 0.56, 0.74))
    # the face's rim (hairline, temples, jaw) kept clear, outside the inner face only: inside it the skin's edges
    # are the features', guarded above
    near_edge = cv2.distanceTransform((skin > 200).astype(np.uint8), cv2.DIST_L2, 5) <= face_width / 40.0
    guard[near_edge & (inner_face(shape, faces) == 0)] = 255
    return guard


def find_spots(rgb, skin, faces, face_width, relative_texture, strength, wide):
    """find_blemishes over the search area (see blemish_guard); on acne-prone skin (ACNE_SPOTS per face found the
    strict way) searched again counting darker-and-redder spots too. Returns the spots, the area searched and
    whether the skin was taken for acne-prone."""
    guard = blemish_guard(skin.shape, faces, face_width, skin, wide)
    spots, core = find_blemishes(rgb, skin, guard, relative_texture, face_width, strength)
    if wide and len(spots) >= ACNE_SPOTS * max(1, len(faces)):
        # The joint search runs more of the skin together, so neighbouring spots can merge into a shape it rejects:
        # it only adds to what the strict search found.
        more, _ = find_blemishes(rgb, skin, guard, relative_texture, face_width, strength, joint=True)
        for spot in more:
            if all(math.hypot(spot[0] - s[0], spot[1] - s[1]) > spot[2] + s[2] for s in spots):
                spots.append(spot)
        spots.sort(key=lambda spot: -spot[3])
        return spots[:MAX_SPOTS], core, True
    return spots, core, False


def _disk(radius):
    span = np.arange(-radius, radius + 1)
    return (span[None, :] ** 2 + span[:, None] ** 2) <= radius ** 2


def heal(rgb, spots, core, face_width):
    """`rgb` with each spot replaced: its tone filled in from around it (inpainting the low band) and real skin
    texture copied from a clean patch next to it (the high band), so the healed skin isn't blurry.
    Returns the healed image and the healed area (uint8 mask)."""
    height, width = rgb.shape[:2]
    low = gaussian(rgb, max(1.0, face_width / 300.0))  # split at about pore size
    high = rgb - low
    region = np.zeros((height, width), np.uint8)
    reaches = []
    for (x, y, radius, _) in spots:
        reach = int(math.ceil(radius * 1.6 + 1))
        cv2.circle(region, (int(round(x)), int(round(y))), reach, 255, -1)
        reaches.append(reach)
    filled = cv2.inpaint(to8(low), region, 3, cv2.INPAINT_TELEA).astype(np.float32) / 255.0
    texture = high.copy()
    for (x, y, _, _), reach in zip(spots, reaches):
        cx, cy = int(round(x)), int(round(y))
        y0, y1, x0, x1 = cy - reach, cy + reach + 1, cx - reach, cx + reach + 1
        if y0 < 0 or x0 < 0 or y1 > height or x1 > width:
            continue
        disk = _disk(reach)
        donor = None
        for distance in (2.4, 3.4):  # the nearest clean patch of skin around the spot
            for angle in range(0, 360, 45):
                dx = int(round(distance * reach * math.cos(math.radians(angle))))
                dy = int(round(distance * reach * math.sin(math.radians(angle))))
                if y0 + dy < 0 or x0 + dx < 0 or y1 + dy > height or x1 + dx > width:
                    continue
                if core[y0 + dy:y1 + dy, x0 + dx:x1 + dx][disk].all() \
                        and not region[y0 + dy:y1 + dy, x0 + dx:x1 + dx][disk].any():
                    donor = (dx, dy)
                    break
            if donor:
                break
        patch = texture[y0:y1, x0:x1]
        if donor:
            patch[disk] = high[y0 + donor[1]:y1 + donor[1], x0 + donor[0]:x1 + donor[0]][disk]
        else:
            patch[disk] *= 0.3  # no clean skin nearby: soften the spot's own texture instead
    healed = np.where(region[..., None] > 0, filled + texture, rgb)
    return np.clip(healed, 0, 1).astype(np.float32), region


# The dual curves, brightening and darkening, both in Luminosity (it keeps saturation from rising as a curve darkens,
# or falling as it brightens): painted on their masks, dodge & burn.
DODGE_CURVE = ((0, 0), (128, 160), (255, 255))
BURN_CURVE = ((0, 0), (128, 96), (255, 255))


def srgb_to_linear(srgb):
    return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(linear):
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055).astype(np.float32)


# Static glow looks: which light glows (linear luminance where it starts and where it is full), how far it spreads
# (Gaussian sigmas as fractions of the short side, each with its share; the first `inner` of them make the inner
# ring of a two-color glow, the rest the outer one), the layer's opacity, the default color and the name.
GLOW_STYLES = {
    # Starts above skin's usual brightness (sRGB ~0.7), so faces get a soft sheen rather than a lift.
    "mist": {"knee": (0.45, 1.0), "spread": ((1 / 150, 0.50), (1 / 50, 0.35), (1 / 17, 0.15)), "inner": 1,
             "opacity": 0.45, "color": "natural", "label": "黑柔"},
    # Only near-white highlights: on film the red halo shows around them, not over skin.
    "halation": {"knee": (0.75, 1.0), "spread": ((1 / 500, 0.6), (1 / 200, 0.4)), "inner": 1,
                 "opacity": 0.7, "color": "film", "label": "胶片光晕"},
}
# Glow colors, in sRGB as a color overlay takes them. `natural` is the light's own color (the overlay is off).
# `film` matches the red-orange halo the halation style had when it was tinted (1, 0.4, 0.15) in linear light.
NATURAL = "natural"
GLOW_COLORS = {
    "white": ("#FFFFFF", "白"), "gold": ("#FFD08A", "暖金"), "film": ("#FF994E", "胶片橙红"),
    "red": ("#FF5A40", "红"), "rose": ("#FF8FB8", "玫瑰粉"), "lavender": ("#B8A0FF", "薰衣草紫"),
    "ice": ("#9CCBFF", "冰蓝"), "teal": ("#6FE3D6", "青"),
}
GLOW_SKIN_KEEP = 0.4  # how much of a cool-colored glow the mask lets onto skin (blue light on a face looks ill)


def bloom(rgb, style, split=False):
    """Light spilling around the bright parts of `rgb` (0-1): the highlights picked out in linear light (where light
    adds up), spread at a few widths for a natural falloff, and encoded back to sRGB. Screened over the photo it
    brightens around the highlights and leaves shadows away from them as they were. Returns a list: the whole glow,
    or with `split` its inner and outer rings."""
    look = GLOW_STYLES[style]
    height, width = rgb.shape[:2]
    linear = srgb_to_linear(rgb)
    low, high = look["knee"]
    t = np.clip((luma(linear) - low) / (high - low), 0, 1)
    light = linear * (t * t * (3 - 2 * t))[..., None]  # picked at full size, so small bright points still count
    sigmas = [(max(1.0, share * min(height, width)), weight) for share, weight in look["spread"]]
    factor = max(1, int(min(sigma for sigma, _ in sigmas) // 3))
    if factor > 1:  # the spread is wide, so it is worked out on a reduced copy (area-averaged: no light lost)
        light = cv2.resize(light, (max(1, width // factor), max(1, height // factor)), interpolation=cv2.INTER_AREA)
    parts = [weight * gaussian(light, sigma / factor) for sigma, weight in sigmas]
    rings = [sum(parts[:look["inner"]]), sum(parts[look["inner"]:])] if split else [sum(parts)]
    if factor > 1:
        rings = [cv2.resize(ring, (width, height), interpolation=cv2.INTER_LINEAR) for ring in rings]
    return [linear_to_srgb(ring) for ring in rings]


def glow_color(name):
    """A glow color from the command line: `natural`, a name from GLOW_COLORS or #RRGGBB."""
    name = name.strip().lower()
    if name == NATURAL:
        return NATURAL
    if name in GLOW_COLORS:
        return GLOW_COLORS[name][0]
    if re.fullmatch(r"#?[0-9a-f]{6}", name):
        return "#" + name.lstrip("#").upper()
    raise argparse.ArgumentTypeError(f"unknown glow color {name!r}: use natural, {', '.join(GLOW_COLORS)} or #RRGGBB")


def glow_spec(text):
    """--glow STYLE[:COLOR[,COLOR]] as (style, colors): one color, or an inner and an outer one."""
    style, _, colors = text.strip().partition(":")
    style = style.strip().lower()
    if style not in ("mist", "halation", "dream"):
        raise argparse.ArgumentTypeError(f"unknown glow style {style!r}: use mist, halation or dream")
    names = [name for name in colors.split(",")] if colors else []
    if style == "dream":
        if [name.strip().lower() for name in names] not in ([], [NATURAL]):
            raise argparse.ArgumentTypeError("dream is a live Gaussian Blur adjustment, and adjustment layers take no "
                                             "color overlay; for a colored haze use mist:<color>")
        return style, ()
    if len(names) > 2:
        raise argparse.ArgumentTypeError("a glow takes one color, or two (inner,outer)")
    return style, tuple(glow_color(name) for name in (names or [GLOW_STYLES[style]["color"]]))


def color_label(color):
    if color == NATURAL:
        return "原色"
    return next((label for value, label in GLOW_COLORS.values() if value == color), color)


def hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))


def is_cool(color):
    """Green, cyan, blue or violet enough to make skin look cold or ill."""
    if color == NATURAL:
        return False
    hue, saturation, _ = colorsys.rgb_to_hsv(*hex_rgb(color))
    return saturation >= 0.2 and 75 / 360 < hue < 300 / 360


def color_overlay(color):
    """A layer's `effects` with a color overlay: on in `color`, or off (in white) for the glow's own color. Compositor
    decodes every field of an effect, so all of them are written."""
    red, green, blue = hex_rgb("#FFFFFF" if color == NATURAL else color)
    return {"colorOverlay": {"enabled": color != NATURAL, "red": round(red, 4), "green": round(green, 4),
                             "blue": round(blue, 4), "opacity": 1}}


def glow_image(glow):
    """A glow (sRGB 0-1) as layer pixels: its strength in alpha and its color, at full strength, in RGB. Screened at
    alpha a, color c adds exactly a * c, the glow; and a color overlay, which keeps alpha, recolors it in place."""
    alpha = to8(glow.max(axis=2))
    strength = alpha.astype(np.float32)[..., None] / 255.0
    color = np.where(strength > 0, np.clip(glow / np.maximum(strength, 1 / 255.0), 0, 1), 1.0)
    return Image.fromarray(np.dstack([to8(color), alpha]), "RGBA")


# ==========================================
# 工程读写 (The project package)
# ==========================================

# ==========================================
# 调整层 (Adjustment layers, computed the way Compositor computes them: its Curves, Levels, Exposure and
# Hue/Saturation tables and its C kernels for Color Balance, Black & White, Gradient Map and Grain, ported line for
# line from github.com/robbietilton/Compositor (MIT), on 8-bit pixels as it works. tests/AdjustReference checks the
# ports against Compositor's own code.)
# ==========================================

def _curve(points, x):
    """Compositor's curve (shape-preserving cubic Hermite through the points) at `x` (0-255, array)."""
    px = np.array([p["x"] for p in points], np.float64)
    py = np.array([p["y"] for p in points], np.float64)
    x = np.asarray(x, np.float64)
    count = len(px)
    i = np.clip(np.searchsorted(px, x, side="right") - 1, 0, count - 2)
    d = np.diff(py) / np.diff(px)
    slopes = np.empty(count)
    slopes[0], slopes[-1] = d[0], d[-1]
    for j in range(1, count - 1):
        slopes[j] = 0.0 if d[j - 1] * d[j] <= 0 else 2 / (1 / d[j - 1] + 1 / d[j])
    h = px[i + 1] - px[i]
    t = np.clip((x - px[i]) / h, 0, 1)
    y = ((2 * t ** 3 - 3 * t ** 2 + 1) * py[i] + (t ** 3 - 2 * t ** 2 + t) * h * slopes[i]
         + (-2 * t ** 3 + 3 * t ** 2) * py[i + 1] + (t ** 3 - t ** 2) * h * slopes[i + 1])
    return np.clip(y, 0, 255)


def _level(r, value):
    """One Levels range (normalized as Compositor does) on values 0-1."""
    black = min(254.0, max(0.0, float(r.get("black", 0))))
    white = min(255.0, max(black + 1, float(r.get("white", 255))))
    gamma = min(9.99, max(0.1, float(r.get("gamma", 1))))
    out_black = min(255.0, max(0.0, float(r.get("outputBlack", 0))))
    out_white = min(255.0, max(0.0, float(r.get("outputWhite", 255))))
    t = np.clip((value * 255 - black) / (white - black), 0, 1)
    return (out_black + np.power(t, 1 / gamma) * (out_white - out_black)) / 255


def _round8(values):
    """C's roundf on values that are never negative (half away from zero; numpy rounds half to even), clamped to 8 bits."""
    return np.clip(np.floor(values + np.float32(0.5)), 0, 255).astype(np.uint8)


def _table_lookup(rgb8, tables):
    """levels_apply on opaque pixels: each channel through its 256-entry table (0-1), rounded to 8 bits."""
    out = np.empty_like(rgb8)
    for channel in range(3):
        out[..., channel] = _round8(np.asarray(tables[channel], np.float32) * np.float32(255))[rgb8[..., channel]]
    return out


def _hsl(red, green, blue):
    high, low = np.maximum(np.maximum(red, green), blue), np.minimum(np.minimum(red, green), blue)
    lightness = (high + low) / 2
    delta = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        saturation = np.where(delta > 0, delta / (1 - np.abs(2 * lightness - 1)), 0.0)
        hue = np.where(high == red, (green - blue) / delta,
                       np.where(high == green, (blue - red) / delta + 2, (red - green) / delta + 4)) * 60
    hue = np.where(delta > 0, np.where(hue < 0, hue + 360, hue), 0.0)
    return hue, np.minimum(1, saturation), lightness


def _rgb(hue, saturation, lightness):
    chroma = (1 - np.abs(2 * lightness - 1)) * saturation
    sector = hue / 60
    second = chroma * (1 - np.abs(np.fmod(sector, 2) - 1))
    base = lightness - chroma / 2
    index = np.trunc(sector).astype(np.int64)
    zero = np.zeros_like(chroma)
    sextants = [index == k for k in range(5)]  # anything else (5, or a negative hue) is the last sextant
    parts = [np.select(sextants, [chroma, second, zero, zero, second], chroma),
             np.select(sextants, [second, chroma, chroma, second, zero], zero),
             np.select(sextants, [zero, zero, second, chroma, chroma], second)]
    return [np.where(saturation <= 0, lightness, np.clip(part + base, 0, 1)) for part in parts]


def _hsv_settings(settings):
    """Hue/Saturation settings from the manifest: the flat [name, value, ...] arrays as dictionaries."""
    hsv = settings.get("hsvSettings")
    if hsv is None:  # projects from before per-range settings: one Master adjustment
        return {"range": "Master", "colorize": bool(settings.get("colorize")), "invert": False,
                "adjustments": {"Master": {"hue": settings.get("hue", 0), "saturation": settings.get("saturation", 0),
                                           "lightness": settings.get("lightness", 0)}}, "bands": dict(HUE_BANDS)}
    pairs = lambda flat: {flat[i]: flat[i + 1] for i in range(0, len(flat) - 1, 2)}
    bands = dict(HUE_BANDS)
    for name, band in pairs(hsv.get("bands") or []).items():
        bands[name] = (band["falloffStart"], band["rangeStart"], band["rangeEnd"], band["falloffEnd"])
    return {"range": hsv.get("range", "Master"), "colorize": bool(hsv.get("colorize")),
            "invert": bool(hsv.get("invertRange")), "adjustments": pairs(hsv.get("adjustments") or []), "bands": bands}


def _forward(start, end):
    """Degrees from `start` forward to `end`, 0-360."""
    delta = np.fmod(np.asarray(end, np.float64) - start, 360)
    return np.where(delta < 0, delta + 360, delta)


def _band_weight(band, hue):
    """How strongly a hue band claims each hue: 1 in its range, ramping through its falloffs, 0 outside."""
    forward = _forward
    falloff_start, range_start, range_end, falloff_end = band
    span = float(forward(falloff_start, falloff_end))
    if span <= 0:
        return np.ones_like(hue)
    position = forward(falloff_start, hue)
    ramp_in, plateau_end = float(forward(falloff_start, range_start)), float(forward(falloff_start, range_end))
    ramp_out = span - plateau_end
    rising = position / ramp_in if ramp_in > 0 else np.ones_like(position)
    falling = (span - position) / ramp_out if ramp_out > 0 else np.ones_like(position)
    weight = np.where(position < ramp_in, rising, np.where(position <= plateau_end, 1.0, falling))
    return np.where(position > span, 0.0, weight)


def hsv_cube(settings):
    """Compositor's 33x33x33 Hue/Saturation lookup, as [blue][green][red] -> RGB 0-1."""
    s = _hsv_settings(settings)
    degrees = np.arange(361, dtype=np.float64)
    shift, saturation_by, lightness_by = (np.zeros(361) for _ in range(3))
    for name, change in s["adjustments"].items():
        hue, sat, light = (float(change.get(k, 0)) for k in ("hue", "saturation", "lightness"))
        if hue == 0 and sat == 0 and light == 0:
            continue
        weight = np.ones(361) if name == "Master" else _band_weight(s["bands"].get(name, HUE_BANDS.get(name)), degrees)
        if s["invert"] and name == s["range"] and name != "Master":
            weight = 1 - weight
        shift += hue * weight
        saturation_by += sat * weight
        lightness_by += light * weight
    step = np.arange(33, dtype=np.float64) / 32
    blue, green, red = np.meshgrid(step, step, step, indexing="ij")
    hue, saturation, lightness = _hsl(red, green, blue)
    if s["colorize"]:
        selected = s["adjustments"].get(s["range"], {})
        hue = np.full_like(hue, np.fmod(float(selected.get("hue", 0)), 360))
        saturation = np.full_like(saturation, min(1, max(0, float(selected.get("saturation", 0)) / 100)))
        amount = float(selected.get("lightness", 0)) / 100
    else:
        index = np.clip(np.floor(hue + 0.5), 0, 360).astype(np.int64)  # Swift's rounding: half away from zero
        amount = lightness_by[index] / 100
        hue = np.fmod(hue + shift[index], 360)
        hue = np.where(hue < 0, hue + 360, hue)
        by = np.clip(saturation_by[index] / 100, -1, 1)
        saturation = np.where(by > 0, np.where(by >= 1, np.where(saturation > 0, 1.0, 0.0),
                                               np.minimum(1, saturation / np.maximum(1 - by, 1e-12))),
                              np.maximum(0, saturation * (1 + by)))
    amount = np.clip(amount, -1, 1)
    lightness = np.where(amount >= 0, lightness + (1 - lightness) * amount, lightness * (1 + amount))
    return np.stack(_rgb(hue, saturation, np.clip(lightness, 0, 1)), axis=-1).astype(np.float32)


def _cube_lookup(rgb8, cube):
    """cube_apply on opaque pixels: trilinear lookup in the 33-point cube, in single precision. The position and
    fraction depend only on the byte, so they come from 256-entry tables."""
    position = np.arange(256, dtype=np.float32) * np.float32(32 / 255.0)
    low_of = np.minimum(position.astype(np.int64), 31)
    fraction_of = (position - low_of).astype(np.float32)
    flat = cube.reshape(-1, 3)
    r, g, b = rgb8[..., 0], rgb8[..., 1], rgb8[..., 2]
    base = (low_of[b] * 33 + low_of[g]) * 33 + low_of[r]
    fr, fg, fb = fraction_of[r][..., None], fraction_of[g][..., None], fraction_of[b][..., None]
    corner = lambda offset: flat[base + offset]
    x00 = corner(0) + (corner(1) - corner(0)) * fr
    x10 = corner(33) + (corner(34) - corner(33)) * fr
    x01 = corner(1089) + (corner(1090) - corner(1089)) * fr
    x11 = corner(1122) + (corner(1123) - corner(1122)) * fr
    y0, y1 = x00 + (x10 - x00) * fg, x01 + (x11 - x01) * fg
    return _round8((y0 + (y1 - y0) * fb).astype(np.float32) * np.float32(255))


def _tonal_weights(v):
    a, b, scale = np.float32(0.25), np.float32(0.333), np.float32(0.7)
    one, half = np.float32(1), np.float32(0.5)
    shadow = np.clip((v - b) / -a + half, 0, 1)
    highlight = np.clip((v + b - one) / a + half, 0, 1)
    middle = np.clip((v - b) / a + half, 0, 1) * np.clip((v + b - one) / -a + half, 0, 1)
    return shadow * scale, middle * scale, highlight * scale


def _color_balance(rgb8, cb):
    names = ("CyanRed", "MagentaGreen", "YellowBlue")
    shadows = [np.float32(cb.get("shadow" + n, 0) / 100) for n in names]
    middles = [np.float32(cb.get("mid" + n, 0) / 100) for n in names]
    highlights = [np.float32(cb.get("highlight" + n, 0) / 100) for n in names]
    if not any(shadows + middles + highlights):
        return rgb8
    c = [rgb8[..., i].astype(np.float32) / np.float32(255) for i in range(3)]
    before = np.float32(0.299) * c[0] + np.float32(0.587) * c[1] + np.float32(0.114) * c[2]
    for i in range(3):
        s, m, h = _tonal_weights(c[i])
        c[i] = np.clip(c[i] + shadows[i] * s + middles[i] * m + highlights[i] * h, 0, 1).astype(np.float32)
    if cb.get("preserveLuminosity", True):
        after = np.float32(0.299) * c[0] + np.float32(0.587) * c[1] + np.float32(0.114) * c[2]
        ratio = np.where(after > np.float32(0.0001), before / np.maximum(after, np.float32(1e-12)), np.float32(1))
        c = [np.where(after > np.float32(0.0001), np.clip(ci * ratio, 0, 1), ci).astype(np.float32) for ci in c]
    return np.dstack([_round8(ci * np.float32(255)) for ci in c])


def _black_white(rgb8, bw):
    weights = [np.float32(bw.get(k, d) / 100) for k, d in (("reds", 40), ("yellows", 60), ("greens", 40), ("cyans", 60),
                                                             ("blues", 20), ("magentas", 80))]
    r, g, b = (np.minimum(np.float32(255), rgb8[..., i].astype(np.float32)) / np.float32(255) for i in range(3))
    high, low = np.maximum(r, np.maximum(g, b)), np.minimum(r, np.minimum(g, b))
    middle = r + g + b - high - low
    primary = np.where(high == r, 0, np.where(high == g, 2, 4))
    secondary = np.where(high == r, np.where(g >= b, 1, 5), np.where(high == g, np.where(r >= b, 1, 3), np.where(g >= r, 3, 5)))
    table = np.array(weights, np.float32)
    gray = np.clip(low + (middle - low) * table[secondary] + (high - middle) * table[primary], 0, 1).astype(np.float32)
    out = [gray, gray, gray]
    saturation = float(bw.get("tintSaturation", 20)) / 100
    if bw.get("tint") and saturation > 0:
        g64 = gray.astype(np.float64)
        chroma = (1.0 - np.abs(2.0 * g64 - 1.0)) * saturation
        hp = np.fmod(float(bw.get("tintHue", 40)), 360.0) / 60.0
        xx = chroma * (1.0 - abs(np.fmod(hp, 2.0) - 1.0))
        zero = np.zeros_like(chroma)
        r1, g1, b1 = [(chroma, xx, zero), (xx, chroma, zero), (zero, chroma, xx), (zero, xx, chroma), (xx, zero, chroma),
                      (chroma, zero, xx)][min(5, int(hp)) if hp >= 0 else 5]
        m = g64 - chroma / 2.0
        out = [np.clip(v + m, 0, 1).astype(np.float32) for v in (r1, g1, b1)]
    return np.dstack([_round8(v * np.float32(255)) for v in out])


def _gradient_map(rgb8, gm):
    dark, light = gm.get("shadows", {}), gm.get("highlights", {})
    if gm.get("reversed"):
        dark, light = light, dark
    t = np.arange(256, dtype=np.float64) / 255
    table = np.stack([np.clip(np.floor((dark.get(k, 0.0) + (light.get(k, 1.0) - dark.get(k, 0.0)) * t) * 255 + 0.5), 0, 255)
                      for k in ("red", "green", "blue")], axis=-1).astype(np.uint8)
    wide = rgb8.astype(np.uint32)
    level = (np.uint32(2126) * wide[..., 0] + np.uint32(7152) * wide[..., 1] + np.uint32(722) * wide[..., 2]
             + np.uint32(5000)) // np.uint32(10000)
    return table[np.minimum(level, 255)]


def _mix32(x):
    with np.errstate(over="ignore"):  # 32-bit wraparound is the point of a hash
        x = x ^ (x >> np.uint32(16))
        x = x * np.uint32(0x7FEB352D)
        x = x ^ (x >> np.uint32(15))
        x = x * np.uint32(0x846CA68B)
        return x ^ (x >> np.uint32(16))


def _lattice(ix, iy, seed):
    with np.errstate(over="ignore"):
        h = _mix32(ix.astype(np.uint32) * np.uint32(0x9E3779B1) ^ _mix32(iy.astype(np.uint32) * np.uint32(0x85EBCA77) ^ seed))
    return ((h & np.uint32(0xFFFF)).astype(np.float32) / np.float32(65535)
            + (h >> np.uint32(16)).astype(np.float32) / np.float32(65535) - np.float32(1))


def _grain_field(u, v, scale, seed):
    cell_x, cell_y = np.floor(u / scale), np.floor(v / scale)
    tx, ty = (u / scale - cell_x).astype(np.float32), (v / scale - cell_y).astype(np.float32)
    tx = tx * tx * (np.float32(3) - np.float32(2) * tx)
    ty = ty * ty * (np.float32(3) - np.float32(2) * ty)
    ix, iy = cell_x.astype(np.int64), cell_y.astype(np.int64)
    n00, n10 = _lattice(ix, iy, seed), _lattice(ix + 1, iy, seed)
    n01, n11 = _lattice(ix, iy + 1, seed), _lattice(ix + 1, iy + 1, seed)
    top, bottom = n00 + (n10 - n00) * tx, n01 + (n11 - n01) * tx
    return (top + (bottom - top) * ty) * np.float32(1.6)


def _grain(rgb8, gs, origin=(0, 0)):
    amount, size, roughness = float(gs.get("amount", 25)), float(gs.get("size", 1.5)), float(gs.get("roughness", 50))
    if not amount > 0:
        return rgb8
    size = size if size > 0 else 1.0
    seed = np.uint32(int(gs.get("seed", 0)) & 0xFFFFFFFF)
    strength = np.float32(1.0 if amount > 100 else amount / 100.0) * np.float32(0.35) * np.float32(255)
    rough = np.float32(0.0 if roughness < 0 else 1.0 if roughness > 100 else roughness / 100.0)
    fine_seed = _mix32(np.uint32(seed ^ np.uint32(0xA511E9B3)))
    detail = max(0.5, size * 0.35)
    height, width = rgb8.shape[:2]
    out = np.empty_like(rgb8)
    u = origin[0] + np.arange(width, dtype=np.float64) + 0.5
    rows = max(1, 1_000_000 // max(1, width))
    for top in range(0, height, rows):  # in bands: the hashing makes several full-size arrays
        v = (origin[1] + np.arange(top, min(height, top + rows), dtype=np.float64) + 0.5)[:, None]
        uu = np.broadcast_to(u[None, :], (len(v), width))
        vv = np.broadcast_to(v, (len(v), width))
        smooth = _grain_field(uu, vv, size, seed)
        noise = smooth + (_grain_field(uu, vv, detail, fine_seed) - smooth) * rough
        band = rgb8[top:top + rows].astype(np.float32)
        level = np.minimum((np.float32(0.2126) * band[..., 0] + np.float32(0.7152) * band[..., 1]
                            + np.float32(0.0722) * band[..., 2]) / np.float32(255), np.float32(1))
        delta = noise * strength * (np.float32(0.4) + np.float32(2.4) * level * (np.float32(1) - level))
        out[top:top + rows] = np.floor(np.clip(band + delta[..., None], 0, 255) + np.float32(0.5)).astype(np.uint8)
    return out



REC709 = np.array([0.2126, 0.7152, 0.0722], np.float32)


def blur_reach(sigma):
    """How far ci_gaussian reads past a pixel, and so how far past its mask a blurring adjustment is computed
    (Compositor's samplingMargin: three radii and a little)."""
    return int(math.ceil(3 * sigma)) + 2


def ci_kernel(sigma):
    """Core Image's Gaussian kernel: each tap is the Gaussian's area over its pixel (what an impulse through
    CIGaussianBlur shows; a point-sampled Gaussian is too peaked at radius 1 and lands a level off in most pixels of a
    noisy image), out to where the rest is nothing, normalized."""
    reach = blur_reach(sigma) - 1
    edges = (np.arange(-reach, reach + 2, dtype=np.float64) - 0.5) / (sigma * math.sqrt(2))
    kernel = np.diff(np.array([math.erf(v) for v in edges]))
    return (kernel / kernel.sum()).astype(np.float32)


def ci_gaussian(image, sigma, border=cv2.BORDER_REPLICATE):
    """A Gaussian blur as Core Image's (ci_kernel), separable, at full size: 24 MP at radius 100 takes about a second,
    and a reduced copy was a few levels off. `border`: what lies beyond the edges (cv2.BORDER_CONSTANT for
    transparency, as an unclamped Core Image blur sees; cv2.BORDER_REPLICATE for edges carried on past the sides)."""
    kernel = ci_kernel(sigma)
    return cv2.sepFilter2D(image, -1, kernel, kernel, borderType=border)


def sampling_margin(settings):
    """How far past its mask an adjustment reads (Compositor's samplingMargin): the blurs' reach, else nothing."""
    kind, blocks = settings.get("kind"), settings.get("filterLayerSettings") or {}
    if kind == "Tonal Contrast":
        return blur_reach(float((blocks.get("tonal") or {}).get("radius", TONAL_DEFAULTS["radius"])))
    if kind == "Sharpen":
        return blur_reach(float((blocks.get("sharpen") or {}).get("radius", SHARPEN_DEFAULTS["radius"])))
    return 0


def _tonal_contrast(rgb8, block):
    """Compositor's Tonal Contrast (AdjustPixels.c adjust_tonal_contrast, its base a Core Image Gaussian blur of the
    layer, unclamped: transparent beyond the edges, which the kernel divides out): the Rec. 709 brightness's
    difference from the blurred brightness, squashed by tanh, added to all three channels, weighted by the blurred
    tone's shadow/midtone/highlight strengths and fading at black and white. 8-bit in, 8-bit out, as Compositor's."""
    values = {key: float(block.get(key, default)) for key, default in TONAL_DEFAULTS.items()}
    if values["amount"] <= 0 or not any(values[key] for key in ("shadows", "midtones", "highlights")):
        return rgb8
    rgb = rgb8.astype(np.float32)
    base = np.rint(ci_gaussian(rgb, values["radius"], cv2.BORDER_CONSTANT))
    kernel = ci_kernel(values["radius"])  # the blurred alpha: the kernel's weight inside the edges, separable
    reach = len(kernel) // 2
    inside = [np.convolve(np.ones(n, np.float64), kernel, mode="full")[reach:reach + n] for n in rgb8.shape[:2]]
    base_alpha = np.rint(np.outer(inside[0], inside[1]) * 255.0).astype(np.float32)
    base_lum = np.minimum(1.0, base / np.maximum(base_alpha, 1.0)[..., None]) @ REC709
    rgb /= 255.0
    lum = rgb @ REC709

    def smooth(low, high, value):
        t = np.clip((value - low) / (high - low), 0, 1)
        return t * t * (3.0 - 2.0 * t)
    shadow, highlight = 1.0 - smooth(0.15, 0.5, base_lum), smooth(0.5, 0.85, base_lum)
    weight = (values["shadows"] * shadow + values["midtones"] * (1.0 - shadow - highlight) + values["highlights"] * highlight) / 100.0
    delta = 0.18 * np.tanh((lum - base_lum) * 6.0) * weight * (values["amount"] / 50.0) * (4.0 * lum * (1.0 - lum))
    out = np.clip(rgb + delta[..., None], 0, 1)
    out[base_alpha <= 0] = rgb[base_alpha <= 0]
    return np.rint(out * 255.0).astype(np.uint8)


def _sharpen(rgb8, block):
    """Compositor's Sharpen: Core Image's unsharp mask on the layer with its edges carried on past every side, the
    difference from a Gaussian blur of `radius` added back at `amount` percent."""
    values = {key: float(block.get(key, default)) for key, default in SHARPEN_DEFAULTS.items()}
    rgb = rgb8.astype(np.float32)
    blurred = ci_gaussian(rgb, values["radius"], cv2.BORDER_REPLICATE)
    return np.clip(np.rint(rgb + (rgb - blurred) * (values["amount"] / 100.0)), 0, 255).astype(np.uint8)


def adjusted(canvas, settings, origin=(0, 0)):
    """What an adjustment layer makes of `canvas` (RGB 0-1, opaque), before its mask, opacity and blend mode: RGB
    0-1, or None for a kind not reproduced here (Motion Blur, Add Noise, Reduce Noise). Gaussian Blur returns
    (premultiplied RGB, alpha): Compositor blurs without extending the edges, so near them the blur fades out.
    `origin` is where `canvas` sits in the document (Grain's pattern is fixed there)."""
    kind = settings.get("kind")
    if kind == "Gaussian Blur":
        sigma = float(settings.get("blurRadius") or 10)
        alpha = gaussian_clear(np.ones(canvas.shape[:2], np.float32), sigma)
        return gaussian_clear(canvas, sigma), alpha
    rgb8 = to8(canvas)
    if kind == "Curves":
        channels = settings["curves"]["channels"]
        x = np.arange(256, dtype=np.float64)
        result = _table_lookup(rgb8, [_curve(channels[0], _curve(channels[c], x)) / 255 for c in (1, 2, 3)])
    elif kind == "Levels":
        ranges = settings["levels"]["ranges"]
        x = np.arange(256, dtype=np.float64) / 255
        result = _table_lookup(rgb8, [_level(ranges[0], _level(ranges[c], x)) for c in (1, 2, 3)])
    elif kind == "Exposure":
        e = settings.get("exposureSettings") or {}
        x = np.arange(256, dtype=np.float64) / 255
        linear = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        linear = np.power(np.maximum(0, linear * 2.0 ** float(e.get("exposure", 0)) + float(e.get("offset", 0))),
                          1 / float(e.get("gamma", 1)))
        table = np.clip(np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.power(linear, 1 / 2.4) - 0.055), 0, 1)
        result = _table_lookup(rgb8, [table] * 3)
    elif kind == "Hue/Saturation":
        result = _cube_lookup(rgb8, hsv_cube(settings))
    elif kind == "Color Balance":
        result = _color_balance(rgb8, settings.get("colorBalanceSettings") or {})
    elif kind == "Black & White":
        result = _black_white(rgb8, settings.get("blackWhiteSettings") or {})
    elif kind == "Gradient Map":
        result = _gradient_map(rgb8, settings.get("gradientMapSettings") or {})
    elif kind == "Grain":
        result = _grain(rgb8, settings.get("grainSettings") or {}, origin)
    elif kind == "Invert":
        result = 255 - rgb8
    elif kind == "Tonal Contrast":
        result = _tonal_contrast(rgb8, (settings.get("filterLayerSettings") or {}).get("tonal") or {})
    elif kind == "Sharpen":
        result = _sharpen(rgb8, (settings.get("filterLayerSettings") or {}).get("sharpen") or {})
    else:
        return None
    return result.astype(np.float32) / 255.0


def gaussian_clear(image, sigma):
    """Gaussian blur with nothing beyond the edges (transparent there), as Core Image blurs an image; wide ones on a
    reduced copy, like `gaussian`."""
    if sigma <= 8:
        return cv2.GaussianBlur(image, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)
    height, width = image.shape[:2]
    factor = max(2, int(sigma // 4))
    small = cv2.resize(image, (max(1, width // factor), max(1, height // factor)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / factor, borderType=cv2.BORDER_CONSTANT)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def folder_mark(name):
    """How the name of one of the GROUPS folders begins: its emoji."""
    return name.split(" ", 1)[0]


def group_of(record):
    """The group (GROUPS) a top-level record belongs to, or None for one that isn't this skill's: the group's folder,
    or a layer or folder whose name begins as the group's loose layers do."""
    name = str(record.get("name", ""))
    for key, folder, begins in GROUPS:
        if (folder and record.get("isGroup") and name.startswith(folder_mark(folder))) or name.startswith(begins):
            return key
    return None


def render_order(layers, top_first=False):
    """(record, depth) in drawing order, as Compositor walks folders: siblings in array order per parent."""
    children = {}
    for record in layers:
        children.setdefault(record.get("parentID"), []).append(record)
    order = []

    def visit(parent, depth):
        siblings = children.get(parent, [])
        for record in reversed(siblings) if top_first else siblings:
            order.append((record, depth))
            if record.get("isGroup") and depth < 64:
                visit(record["id"], depth + 1)

    visit(None, 0)
    return order


class Project:
    def __init__(self, path, manifest):
        self.path = path
        self.images = os.path.join(path, "images")
        self.manifest = manifest
        self.layers = manifest["layers"]
        self.added = []          # records added this run, for the report
        self.refreshed = []      # (existing layer, what was done) whose pixels or mask this run redoes, for the report
        self.staging = os.path.join(path, STAGING)
        self.written = []        # files written this run, removed again if the build fails
        self._staged = {}        # files this run adds or redoes: file name -> where it waits until commit
        self._saving = {}        # PNGs still being written in the background: file name -> (future, image)
        self._savers = None
        self._ahead = {}         # PNGs being decoded ahead of a composite: file name -> future
        self._top_up = None
        self.prune = False       # delete images the manifest no longer references (--force)
        self.created_dir = False
        self.fresh = False       # made by this run: its format version follows what goes in (commit)
        self.compat = False      # --8bit: only what Compositor before 1.5 reads (format 11, pixel layers)
        self.spots = []          # healed spots, for the check image
        self.hair = None         # hair taken out of the skin mask, for the check image
        self.paintings = {}      # masks painted in from the landmarks, for the check image
        self.glow_check = None   # the frame before and after the glow, for the check image
        self.look_check = None   # the frame before and after a color grade, for the check image
        self.use_vision = True
        self.skin_changed = False
        self._base = None
        self._faces = None
        self._people_small = None  # Vision's person matte at its own resolution
        self._people = None
        self._parts = None         # hair and clothes from the segmentation model
        self.use_parsing = True
        self._skin = None
        self._reach = None       # the skin plus the rest of the people's skin, for a smoothing brushed on by hand
        self._manifest_stamp = None  # manifest.json's (mtime, size) when opened: commit refuses to overwrite a newer one
        self._work = None        # what Compositor draws up to and including `_work_anchor` (RGB, 0-1)
        self._work_anchor = None

    @property
    def width(self):
        return self.manifest["width"]

    def allows(self, kind):
        """Whether an adjustment layer of `kind` can go in this project: its format version has it (KIND_VERSIONS),
        or the project is new this run, so its version follows what goes in (commit); not with --8bit, which asks for
        the shapes Compositor before 1.5 reads. An existing project keeps its version: Compositor writes the newest
        (20) whenever it saves, so a project the user has saved there takes every kind."""
        return int(self.manifest.get("version") or 0) >= KIND_VERSIONS.get(kind, 7) or (self.fresh and not self.compat)

    @property
    def height(self):
        return self.manifest["height"]

    @classmethod
    def open(cls, path):
        manifest_path = os.path.join(path, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise BuildError(f"no Compositor project at {path} (no manifest.json); create one with --init <photo>")
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError) as error:
            raise BuildError(f"cannot read {manifest_path}: {error}")
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT \
                or not isinstance(manifest.get("layers"), list):
            raise BuildError(f"{manifest_path} is not a Compositor project manifest")
        version = manifest.get("version")
        if isinstance(version, int) and version > NEWEST_VERSION:
            warn(f"the project is Compositor format version {version}, newer than the {NEWEST_VERSION} this skill knows: "
                 f"it is checked by the rules of {NEWEST_VERSION}, which a newer Compositor may have changed")
        project = cls(path, manifest)
        project._manifest_stamp = manifest_stamp(manifest_path)
        return project

    @classmethod
    def create(cls, path, photo, force, deep=True):
        manifest_path = os.path.join(path, "manifest.json")
        previous = None
        if os.path.exists(path):
            if not os.path.isdir(path):
                raise BuildError(f"{path} exists and is not a folder")
            if os.listdir(path):
                if not force:
                    raise BuildError(f"{path} already exists. Pass --force to replace it (its current layers will be "
                                     f"deleted), or leave out --init to add layers to it")
                try:
                    with open(manifest_path, encoding="utf-8") as f:
                        previous = json.load(f)
                except (OSError, ValueError):
                    previous = None
        if not path.endswith(".comp"):
            warn("Compositor opens project folders named *.comp; this one will not open as a document")
        image, resolution = load_photo(photo)
        # More than 8 bits a channel stay so, as a 16-bit layer (format 12, Compositor 1.5); the layers computed from
        # it are 8-bit, from the same pixels.
        imported = import_deep(photo) if deep and deep_photo(photo) else None
        width, height = (imported[1].shape[1], imported[1].shape[0]) if imported else image.size
        if width > MAX_SIDE or height > MAX_SIDE:
            if imported:
                shutil.rmtree(os.path.dirname(imported[0]), ignore_errors=True)
            raise BuildError(f"{width}x{height} is larger than Compositor's {MAX_SIDE} px limit per side")
        created = not os.path.exists(path)
        os.makedirs(os.path.join(path, "images"), exist_ok=True)
        manifest = {
            "format": FORMAT, "version": 12 if imported else FORMAT_VERSION, "colorSpace": "sRGB",
            # Replacing a project keeps its identity, so an open window reloads it instead of losing track.
            "documentID": (previous or {}).get("documentID") or new_id(),
            "width": width, "height": height, "resolution": resolution, "layers": [],
        }
        project = cls(path, manifest)
        project.created_dir = created
        project.prune = previous is not None or force
        project.fresh, project.compat = True, not deep
        if imported:
            try:
                record = project.file_layer("原图 (Original)", imported[0])
            finally:
                shutil.rmtree(os.path.dirname(imported[0]), ignore_errors=True)
            rgba = imported[1].astype(np.float32) / 65535.0
        else:
            record = project.pixel_layer("原图 (Original)", image)
            rgba = np.asarray(image, dtype=np.float32) / 255.0
        project.layers.append(record)
        manifest["activeLayerID"] = record["id"]
        project._work = blend(np.ones_like(rgba[..., :3]), rgba[..., :3], rgba[..., 3], "Normal")
        project._work_anchor = record["id"]
        project._base = to8(project._work)
        say(f"[OK] New project {path} ({width}x{height}, {resolution} ppi{', 16-bit' if imported else ''})")
        return project

    # ---- what Compositor draws: the pixels new layers are computed from ----

    def base_record(self):
        for record, _ in render_order(self.layers):
            if record.get("imageFile") and not record.get("isGroup"):
                return record
        raise BuildError("the project has no pixel layer to work from; its bottom layer should be the photo")

    def base_rgb(self):
        """The bottom (photo) layer as it sits on the canvas, over white, 8-bit RGB."""
        if self._base is None:
            self._base = to8(self.composite_through(self.base_record()))
        return self._base

    def place(self, rgb, alpha, record):
        """A layer's pixels and coverage moved where its transform puts them on the canvas (rgb may be None)."""
        transform = record.get("transform", {})
        (x, y), (w, h) = transform.get("origin", [0, 0]), transform.get("size", [self.width, self.height])
        if transform.get("rotation") or transform.get("flipX") or transform.get("flipY"):
            warn(f"'{record.get('name')}' is rotated or flipped; new layers are computed as if it weren't", once=True)
        if (x, y, w, h) == (0, 0, self.width, self.height) and alpha.shape[:2] == (self.height, self.width):
            return rgb, alpha
        size = (max(1, round(w)), max(1, round(h)))
        left, top = round(x), round(y)
        x0, y0 = max(0, left), max(0, top)
        x1, y1 = min(self.width, left + size[0]), min(self.height, top + size[1])
        placed = []
        for plane, channels in ((rgb, 3), (alpha, 1)):
            if plane is None:
                placed.append(None)
                continue
            plane = cv2.resize(plane, size, interpolation=cv2.INTER_LINEAR)
            canvas = np.zeros((self.height, self.width, channels) if channels > 1 else (self.height, self.width),
                              np.float32)
            if x1 > x0 and y1 > y0:
                canvas[y0:y1, x0:x1] = plane[y0 - top:y1 - top, x0 - left:x1 - left]
            placed.append(canvas)
        return placed[0], placed[1]

    def adjustment_coverage(self, record):
        """Where an adjustment layer acts: everywhere without a mask, else its mask stretched over its transform."""
        if not record.get("maskFile") or record.get("maskEnabled") is False:
            return np.ones((self.height, self.width), np.float32)
        with self._image(record["maskFile"]) as source:
            mask = np.asarray(source.convert("L"), dtype=np.float32) / 255.0
        _, coverage = self.place(None, mask, record)
        return coverage

    def layer_pixels(self, record):
        """A pixel layer as it lands on the canvas: RGB and coverage (its alpha times its mask), 0-1."""
        with self._image(record["imageFile"]) as source:
            rgba = np.asarray(source.convert("RGBA"), dtype=np.float32) / 255.0
        alpha = rgba[..., 3]
        if record.get("maskFile") and record.get("maskEnabled", True):
            if record.get("maskLinked") is False:
                warn(f"'{record.get('name')}' has an unlinked mask; it is applied as if linked", once=True)
            with self._image(record["maskFile"]) as source:
                mask = source.convert("L").resize((rgba.shape[1], rgba.shape[0]), Image.BILINEAR)
            alpha = alpha * (np.asarray(mask, dtype=np.float32) / 255.0)
        return self.place(np.ascontiguousarray(rgba[..., :3]), np.ascontiguousarray(alpha), record)

    def _is_noop(self, record):
        """An adjustment whose mask hides it completely (the black 1x1 masks this skill writes)."""
        if not record.get("maskFile") or record.get("maskEnabled") is False:
            return False
        try:
            with self._image(record["maskFile"]) as source:
                return source.convert("L").getextrema()[1] == 0
        except OSError:
            return False

    def composite_through(self, anchor):
        """What Compositor draws up to and including `anchor`, over white: RGB 0-1. Pixel layers in every blend
        mode, with their masks, folders and color overlays, and adjustment layers as Compositor computes them
        (see `adjusted`); what isn't reproduced (Motion Blur, Add Noise, clipping, layer effects but the color
        overlay) is left out with a warning."""
        by_id = {record["id"]: record for record in self.layers}
        drawn = []
        for record, _ in render_order(self.layers):
            if not record.get("isGroup"):
                drawn.append(record)
            if record["id"] == anchor["id"]:
                break
        canvas = np.ones((self.height, self.width, 3), np.float32)
        with self._reading_ahead([name for record in drawn for name in (record.get("imageFile"), record.get("maskFile")) if name]):
            for record in drawn:
                canvas = self._draw(canvas, record, by_id)
        return canvas

    @contextlib.contextmanager
    def _reading_ahead(self, names, ahead=3):
        """Decodes the next few images in the background while the current layer is drawn (Pillow lets go of the
        GIL while it decodes), so a 24 MP project isn't read one PNG at a time."""
        queue = [name for name in dict.fromkeys(names) if name not in self._saving]
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=ahead)

        def load(name):
            return read_image(self.file_path(name))

        def top_up():
            while queue and len(self._ahead) < ahead:
                name = queue.pop(0)
                self._ahead[name] = pool.submit(load, name)
        self._ahead, self._top_up = {}, top_up
        top_up()
        try:
            yield
        finally:
            queue.clear()
            for future in self._ahead.values():
                future.cancel()
            pool.shutdown(wait=True)
            self._ahead, self._top_up = {}, None

    def _draw(self, canvas, record, by_id, quiet=False):
        visible, opacity, parent = record.get("isVisible", True), record.get("opacity", 1), record.get("parentID")
        masked = []
        while parent:  # a folder hides, fades and masks what is inside it
            folder = by_id.get(parent, {})
            visible = visible and folder.get("isVisible", True)
            opacity *= folder.get("opacity", 1)
            if folder.get("maskFile") and folder.get("maskEnabled") is not False:
                masked.append(folder)
            parent = folder.get("parentID")
        if not visible or opacity <= 0:
            return canvas
        def cover():  # opacity times the folders' masks, read only for a layer that is drawn
            return math.prod((self.adjustment_coverage(folder) for folder in masked), start=opacity)
        settings = record.get("adjustment")
        if settings is not None:
            if self._is_noop(record):
                return canvas
            coverage = self.adjustment_coverage(record) * cover()
            if settings.get("kind") != "Gaussian Blur":  # only where the mask lets it act (and, for a blur, as far
                rows, columns = np.flatnonzero(coverage.any(axis=1)), np.flatnonzero(coverage.any(axis=0))  # as it reads)
                if not len(rows):
                    return canvas
                margin = sampling_margin(settings)
                box = (slice(max(0, rows[0] - margin), min(self.height, rows[-1] + 1 + margin)),
                       slice(max(0, columns[0] - margin), min(self.width, columns[-1] + 1 + margin)))
                if (box[0].stop - box[0].start) * (box[1].stop - box[1].start) < coverage.size:
                    inside = self._draw_adjustment(canvas[box], settings, record, coverage[box], quiet,
                                                   origin=(box[1].start, box[0].start))
                    out = canvas.copy()
                    out[box] = inside
                    return out
            return self._draw_adjustment(canvas, settings, record, coverage, quiet)
        if not record.get("imageFile"):
            return canvas
        mode = record.get("blendMode", "Normal")
        if mode not in BLENDS:
            warn(f"'{record.get('name')}' blends in {mode}, computed here as Normal", once=True)
            mode = "Normal"
        if record.get("maskSourceID"):
            warn(f"clipping of '{record.get('name')}' is ignored when computing new layers", once=True)
        rgb, alpha = self.layer_pixels(record)
        return blend(canvas, self._overlaid(rgb, record), alpha * cover(), mode)

    def _draw_adjustment(self, canvas, settings, record, coverage, quiet, origin=(0, 0)):
        """An adjustment layer over `canvas` (placed at `origin` in the document), through `coverage`."""
        result = adjusted(canvas, settings, origin)
        if result is None:
            if not quiet:
                warn(f"a {settings.get('kind')} adjustment ('{record.get('name')}') isn't reproduced here, so it is "
                     f"left out of computed layers and renders", once=True)
            return canvas
        mode = record.get("blendMode", "Normal")
        if mode not in BLENDS:
            warn(f"'{record.get('name')}' blends in {mode}, computed here as Normal", once=True)
            mode = "Normal"
        if isinstance(result, tuple):  # a blur, premultiplied, fading out at the edges
            premultiplied, alpha = result
            if mode == "Normal":  # drawn over what is below: where the blur is thin, that shows through
                return np.clip(canvas * (1 - (alpha * coverage)[..., None])
                               + premultiplied * coverage[..., None], 0, 1).astype(np.float32)
            result = premultiplied / np.maximum(alpha, 1e-6)[..., None]  # other modes blend its colors
        return blend(canvas, result, coverage, mode)

    @staticmethod
    def _overlaid(rgb, record):
        """A layer's colors under its color overlay, as Compositor draws it: moved toward the overlay's color by its
        opacity, alpha kept. Its other effects (stroke, shadows, glows) are left out."""
        effects = record.get("effects") if isinstance(record.get("effects"), dict) else {}
        overlay = effects.get("colorOverlay")
        if isinstance(overlay, dict) and overlay.get("enabled") is not False and overlay.get("opacity", 1) > 0:
            color = np.array([overlay.get(key, 0) for key in ("red", "green", "blue")], np.float32)
            rgb = rgb + (color - rgb) * np.float32(overlay.get("opacity", 1))
        others = sorted(kind for kind, effect in effects.items()
                        if kind != "colorOverlay" and isinstance(effect, dict) and effect.get("enabled") is not False)
        if others:
            warn(f"effects of '{record.get('name')}' ({', '.join(others)}) are left out when computing new layers",
                 once=True)
        return rgb

    def render(self):
        """The whole project as Compositor exports it, flattened over white: 8-bit RGB."""
        drawn = [record for record, _ in render_order(self.layers) if not record.get("isGroup")]
        return to8(self.composite_through(drawn[-1])) if drawn else np.full((self.height, self.width, 3), 255, np.uint8)

    def skin_walk(self):
        """The photo and what sits right above it as the skin stack, bottom to top: the frequency-separation and skin
        folders, and in projects of earlier versions loose low/high and skin layers."""
        walk = [self.base_record()]
        marks = (folder_mark(FOLDER_NAMES["freq_sep"]), folder_mark(FOLDER_NAMES["skin"]))
        for record in self.siblings_above(walk[0]):
            name = str(record.get("name", ""))
            if not (name.startswith(marks) if record.get("isGroup") else name.startswith(SKIN_STACK)):
                break
            walk.append(record)
        return walk

    def skin_anchor(self):
        """The top of the skin stack: the last layer drawn in it. Never a folder: composite_through stops at its
        anchor, and a folder is drawn before what is in it."""
        anchor = None
        for record in self.skin_walk():
            if not record.get("isGroup"):
                anchor = record
                continue
            drawn = [other for other in self._inside(record) if not other.get("isGroup")]
            anchor = drawn[-1] if drawn else anchor
        return anchor

    def work(self):
        """What Compositor draws up to the top of the skin stack (RGB 0-1): what skin layers are computed from."""
        anchor = self.skin_anchor()
        if self._work is None or self._work_anchor != anchor["id"]:
            self._work = self.composite_through(anchor)
            self._work_anchor = anchor["id"]
        return self._work

    def composite_top(self):
        """What Compositor draws under the top of the stack: the skin stack's composite carried up through
        everything above it."""
        anchor = self.skin_anchor()
        canvas = self.work()
        by_id = {record["id"]: record for record in self.layers}
        above = False
        for record, _ in render_order(self.layers):
            if above and not record.get("isGroup"):
                canvas = self._draw(canvas, record, by_id, quiet=True)
            if record["id"] == anchor["id"]:
                above = True
        return canvas

    def stack_skin_layer(self, record, rgb, alpha, mode):
        """Puts `record` on top of the skin stack, in the skin folder (made first when the top of the stack isn't
        one), and keeps the in-memory composite in step with it."""
        below = self.work()
        top = self.skin_walk()[-1]
        if top.get("isGroup") and str(top.get("name", "")).startswith(folder_mark(FOLDER_NAMES["skin"])):
            self._warn_hidden(top)
            self.insert_into(top, [record])
        else:
            folder = self._group_folder("skin", [record])
            self.insert_above(top, [folder])
            self.insert_into(folder, [record])
        self._work = blend(below, rgb, alpha, mode)
        self._work_anchor = record["id"]
        self.skin_changed = True

    def faces(self):
        if self._faces is None:
            self._faces, self._people_small = detect(self.base_rgb(), self.use_vision)
            source = "Apple Vision, with landmarks" if landmark_faces(self._faces) else "OpenCV, no landmarks"
            say(f"[OK] Faces found: {len(self._faces)} ({source})")
        return self._faces

    def parts(self):
        """Hair and clothes around the faces from the segmentation model (see parse_people), or None."""
        if self._parts is None:
            maps = None
            if self.use_parsing and self.faces():
                interpreter = parsing_model()
                if interpreter is not None:
                    maps = parse_people(self.base_rgb(), self.faces(), interpreter)
                    say("[OK] Hair and skin told apart by the segmentation model")
            self._parts = maps if maps is not None else False
        return None if self._parts is False else self._parts

    def people(self):
        """Vision's person segmentation at the canvas size (0-1, edges following the photo), or None without it,
        and when it leaves out the face that was found: then it can't be trusted, and bounding the skin mask by it
        would quietly switch every skin layer off."""
        if self._people is None:
            matte = None
            if self.use_vision:
                faces = self.faces()  # the matte comes with the faces
                if self._people_small is not None and self._people_small.max() > 0:
                    matte = person_matte(self._people_small, self.base_rgb())
                    if faces:
                        x, y, w, h = (int(round(v)) for v in max(faces, key=lambda face: face[2]))
                        inside = matte[max(0, y):max(0, y + h), max(0, x):max(0, x + w)]
                        if inside.size and inside.mean() < 0.5:
                            warn("Apple Vision's person segmentation leaves out the face it found, so it isn't used: "
                                 "the skin mask isn't bounded by it", once=True)
                            matte = None
            self._people = matte if matte is not None else False
        return None if self._people is False else self._people

    def painted(self, name, shape_of):
        """A mask painted in from the landmarks (kept for the check images) and how the layer name describes it;
        a black 1x1 mask to paint by hand when there are no landmarks or nothing to paint (a closed mouth)."""
        faces = self.faces()
        if landmark_faces(faces):
            mask = shape_of((self.height, self.width), faces)
            if mask.max() > 0:
                self.paintings[name] = mask
                return Image.fromarray(mask), "已按五官预涂 · 可擦改"
        return solid_mask(0), "黑蒙版涂白"

    def face_width(self):
        faces = self.faces()
        return max(face[2] for face in faces) if faces else min(self.width, self.height) / 4.0

    def skin(self):
        """The skin mask (8-bit) and its label, from the photo."""
        if self._skin is None:
            mask, label, self.hair = skin_mask(self.base_rgb(), self.faces(), self.people(), self.parts())
            self._skin = (mask, label)
        return self._skin

    def skin_reach(self):
        """Where a smoothing layer brushed on by hand has smoothing to show (8-bit, feathered): the skin mask plus
        every skin-toned part of the people in the photo (neck, hands, shoulders), without the hair and clothes the
        segmentation model tells, so a brush stroke beyond the face reveals smoothed skin, not the photo."""
        if self._reach is None:
            skin, _ = self.skin()
            rgb, faces, people = self.base_rgb(), self.faces(), self.people()
            tone = cv2.inRange(cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb), (0, 133, 77), (255, 173, 127))
            tone[cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY) < 45] = 0
            if faces:
                distance, brightness = skin_color(rgb, faces, tone, people)
                tone[(distance < 3.5) & (brightness > 0.1)] = 255
            if people is not None:
                tone[people < 0.5] = 0
            if self.hair is not None:
                tone[self.hair > 0] = 0
            parts = self.parts()
            if parts is not None:
                tone[parts["hair"] > 127] = 0
                tone[parts["clothes"] > 127] = 0
            kernel = odd(max(3, round(min(self.height, self.width) / 400.0)))
            tone = cv2.morphologyEx(tone, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel, kernel)))
            feather = odd(max(9, round(min(self.height, self.width) / 180.0)))
            self._reach = np.maximum(skin, cv2.GaussianBlur(tone, (feather, feather), 0))
        return self._reach


    # ---- records ----

    def _stage(self, filename):
        """Where a file this run writes waits until commit: the hidden folder STAGING in the package, outside images/.
        Compositor fingerprints an open project by its manifest and the names and sizes of the files in images/,
        and on every change it sees reloads the whole project, decoding every layer, backing off up to half a
        minute while it is still busy with the last one; files landing in images/ over a run had it reload again
        and again, so the new layers showed late. Here they wait, and commit moves them all in at once. The folder
        is made again on each call: Compositor's own save replaces the package, and this folder with it."""
        os.makedirs(self.staging, exist_ok=True)
        self._staged[filename] = os.path.join(self.staging, filename)
        return self._staged[filename]

    def file_path(self, filename):
        """A layer's image or mask file as it is now: where it waits if this run wrote it, else in images/."""
        return self._staged.get(filename) or os.path.join(self.images, filename)

    def _check_stamp(self):
        """Raises when Compositor saved the project while this ran (the user pressed ⌘S, or the app's save landed
        late): the manifest in memory is older than the one on disk, and overwriting it would silently undo what
        that save held; its save also replaced the package, staged files and all. Nothing is changed; the caller
        runs again on the saved project."""
        if self._manifest_stamp is not None and manifest_stamp(os.path.join(self.path, "manifest.json")) != self._manifest_stamp:
            raise BuildError("project changed on disk while this ran (saved by Compositor?): nothing was changed, "
                             "run it again")

    def save_png(self, image, filename, path=None):
        """Writes a layer's PNG in the background (PNG compression takes a while at 24 MP and needs no Python, so
        the next module goes on meanwhile) to where it waits for commit; until it is on disk, reads of it get the
        image from memory."""
        path = path or self._stage(filename)
        self.written.append(path)
        image = image.copy()  # the caller may go on changing its own
        if self._savers is None:
            self._savers = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2) - 1)))
        self._saving[filename] = (self._savers.submit(image.save, path, format="PNG", compress_level=PNG_COMPRESS_LEVEL), image)
        for name, (future, _) in list(self._saving.items()):
            if future.done():
                try:
                    future.result()  # a failed write stops the build here
                except OSError:
                    self._check_stamp()  # the folder gone with a save of Compositor's: the retryable error, not this
                    raise
                del self._saving[name]

    def stage_png(self, image, filename):
        """New pixels for an existing layer's image: written where it waits (reads of `filename` get them
        meanwhile) and swapped in by commit."""
        self.save_png(image, filename)

    def mask_of(self, record):
        """A layer's mask at the canvas size (8-bit), or None without one."""
        if not record.get("maskFile"):
            return None
        with self._image(record["maskFile"]) as source:
            mask = source.convert("L")
            if mask.size != (self.width, self.height):
                mask = mask.resize((self.width, self.height), Image.BILINEAR)
            return np.array(mask)

    def finish_saving(self, check=True):
        """Waits for the background writes; raises the first failure unless `check` is off."""
        pending, self._saving = self._saving, {}
        for future, _ in pending.values():
            try:
                future.result()
            except Exception:
                if check:
                    self._check_stamp()
                    raise

    @contextlib.contextmanager
    def _image(self, filename):
        """A layer's image or mask: from memory while it is being written, else from the package."""
        pending = self._saving.get(filename)
        early = self._ahead.pop(filename, None)
        if self._top_up:
            self._top_up()
        if pending is not None:
            yield pending[1]
        else:
            image = early.result() if early is not None else read_image(self.file_path(filename))
            try:
                yield image
            finally:
                image.close()

    def _record(self, name, *, visible, opacity, blend):
        return {"id": new_id(), "name": name, "isVisible": visible, "isGroup": False, "opacity": opacity,
                "blendMode": blend, "transform": canvas_transform(self.width, self.height)}

    def _attach_mask(self, record, mask):
        filename = f"{record['id']}.mask.png"
        self.save_png(mask if mask.mode == "L" else mask.convert("L"), filename)
        record["maskFile"] = filename
        record["maskEnabled"] = True

    def pixel_layer(self, name, image, *, blend="Normal", opacity=1.0, visible=True, mask=None, parent=None,
                    effects=None):
        record = self._record(name, visible=visible, opacity=opacity, blend=blend)
        record["imageFile"] = f"{record['id']}.png"
        self.save_png(image if image.mode == "RGBA" else image.convert("RGBA"), record["imageFile"])
        if parent is not None:
            record["parentID"] = parent["id"]
        if mask is not None:
            self._attach_mask(record, mask)
        if effects is not None:
            record["effects"] = effects
        self.added.append(record)
        return record

    def deep_base(self):
        """The photo layer's own pixels when it is a 16-bit layer filling the canvas (RGB 0-1 over white, float32),
        else None."""
        record = self.base_record()
        path = self.file_path(record.get("imageFile") or "")
        transform = record.get("transform") or {}
        try:
            if png_header(path)[2] != 16:
                return None
        except (OSError, ValueError):
            return None
        if (list(transform.get("origin", [])), list(transform.get("size", []))) != ([0, 0], [self.width, self.height]) \
                or transform.get("rotation") or transform.get("flipX") or transform.get("flipY"):
            return None
        pixels = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
        if pixels is None or pixels.ndim != 3 or pixels.shape[:2] != (self.height, self.width):
            return None
        rgba = cv2.cvtColor(pixels, cv2.COLOR_BGRA2RGBA if pixels.shape[2] == 4 else cv2.COLOR_BGR2RGB)
        rgba = rgba.astype(np.float32) / 65535.0
        if rgba.shape[2] == 3:
            return rgba
        return blend(np.ones_like(rgba[..., :3]), rgba[..., :3], rgba[..., 3], "Normal")

    def _write_deep_png(self, rgb, target, name):
        """`rgb` (0-1) as an opaque 16-bit RGBA PNG at `target`."""
        pixels = np.rint(np.clip(rgb, 0, 1) * 65535.0).astype(np.uint16)
        pixels = np.dstack([pixels[..., ::-1], np.full(pixels.shape[:2], 65535, np.uint16)])  # BGRA for OpenCV
        self.written.append(target)
        ok, encoded = cv2.imencode(".png", pixels, [cv2.IMWRITE_PNG_COMPRESSION, PNG_COMPRESS_LEVEL])
        if not ok:
            raise BuildError(f"could not encode the 16-bit layer '{name}'")
        encoded.tofile(target)

    def deep_layer(self, name, rgb, *, blend="Normal", opacity=1.0, mask=None, parent=None):
        """A 16-bit pixel layer from `rgb` (0-1), opaque pixels."""
        record = self._record(name, visible=True, opacity=opacity, blend=blend)
        record["imageFile"] = f"{record['id']}.png"
        if parent is not None:
            record["parentID"] = parent["id"]
        self._write_deep_png(rgb, self._stage(record["imageFile"]), name)
        if mask is not None:
            self._attach_mask(record, mask)
        self.added.append(record)
        return record

    def stage_deep_png(self, rgb, filename):
        """stage_png for a 16-bit layer: `rgb` (0-1) written as 16 bits where commit swaps it in from."""
        self._write_deep_png(rgb, self._stage(filename), filename)

    def file_layer(self, name, source):
        """A pixel layer whose image is an existing PNG, copied in as it is (the 16-bit photo)."""
        record = self._record(name, visible=True, opacity=1.0, blend="Normal")
        record["imageFile"] = f"{record['id']}.png"
        target = self._stage(record["imageFile"])
        self.written.append(target)
        shutil.copyfile(source, target)
        self.added.append(record)
        return record

    def adjustment_layer(self, name, settings, *, blend="Normal", opacity=1.0, visible=True, mask=None, parent=None):
        record = self._record(name, visible=visible, opacity=opacity, blend=blend)
        record["adjustment"] = settings  # no imageFile: it re-colors whatever is below it
        if parent is not None:
            record["parentID"] = parent["id"]
        if mask is not None:
            self._attach_mask(record, mask)
        self.added.append(record)
        return record

    def folder(self, name, *, visible=True):
        record = self._record(name, visible=visible, opacity=1.0, blend="Normal")
        record["isGroup"] = True
        self.added.append(record)
        return record

    # ---- placing records in the stack ----
    # Only the order of the records in one folder (or at the top level) decides how Compositor draws them, so a
    # record goes after the last one already in its folder, wherever the layer list keeps the folder's contents.

    def _index(self, record):
        return next(i for i, other in enumerate(self.layers) if other["id"] == record["id"])

    def _inside(self, folder):
        """What is inside `folder` (at any depth), in drawing order."""
        order = render_order(self.layers)
        for position, (record, depth) in enumerate(order):
            if record["id"] == folder["id"]:
                inside = []
                for other, other_depth in order[position + 1:]:
                    if other_depth <= depth:
                        break
                    inside.append(other)
                return inside
        return []

    def _insert(self, index, records, parent):
        """At `index` in the layer list, in the folder `parent` (an id, or None for the top level). A record that has
        a parentID keeps it: the contents of a folder among `records`."""
        for offset, record in enumerate(records):
            if parent and not record.get("parentID"):
                record["parentID"] = parent
            self.layers.insert(index + offset, record)

    def _end_of(self, record):
        """The index in the layer list just after `record` and everything in it."""
        ids = {record["id"]} | {other["id"] for other in self._inside(record)}
        return max(i for i, other in enumerate(self.layers) if other["id"] in ids) + 1

    def insert_above(self, anchor, records):
        """Right above `anchor`, in the same folder: before the next record in that folder."""
        parent = anchor.get("parentID")
        later = [i for i in range(self._index(anchor) + 1, len(self.layers)) if self.layers[i].get("parentID") == parent]
        self._insert(min([self._end_of(anchor)] + later[:1]), records, parent)

    def insert_into(self, folder, records):
        """On top of what is in `folder`."""
        self._insert(self._end_of(folder), records, folder["id"])

    def siblings_above(self, anchor):
        parent = anchor.get("parentID")
        index = self._index(anchor)
        return [record for record in self.layers[index + 1:] if record.get("parentID") == parent]

    def frequency_pair(self):
        """The low/high pair right above the photo, in its folder or (earlier versions) loose, if there is one."""
        above = self.siblings_above(self.base_record())
        if above and above[0].get("isGroup") \
                and str(above[0].get("name", "")).startswith(folder_mark(FOLDER_NAMES["freq_sep"])):
            above = [record for record in self._inside(above[0]) if not record.get("isGroup")]
        pair = above[:2]
        if len(pair) == 2 and all(r.get("imageFile") for r in pair) \
                and (pair[0].get("blendMode") or "Normal") == "Normal" and pair[1].get("blendMode") == "Linear Light":
            return pair
        return None

    def _top_level_index(self, key):
        """Where a new top-level record of group `key` goes in the layer list: under the top-level records of the
        groups above it in GROUPS (the observation folder on top, the grain over the glow, and so on), over the rest."""
        index = len(self.layers)
        roots = [i for i, record in enumerate(self.layers) if not record.get("parentID")]
        while roots:
            group = group_of(self.layers[roots[-1]])
            if group is None or RANK[group] <= RANK[key]:
                break
            index = roots.pop()
        return index

    def group_folder(self, key):
        """The folder of group `key` that new layers go in, the topmost if the user made copies; None without one."""
        mark = folder_mark(FOLDER_NAMES[key])
        found = [record for record, _ in render_order(self.layers)
                 if record.get("isGroup") and str(record.get("name", "")).startswith(mark)]
        return found[-1] if found else None

    def _group_folder(self, key, records):
        """A new folder for group `key`, listed in the report before `records`, the first layers going in it."""
        folder = self.folder(FOLDER_NAMES[key])
        self.added.remove(folder)
        first = next((i for i, record in enumerate(self.added) if any(record is new for new in records)), len(self.added))
        self.added.insert(first, folder)
        return folder

    def _warn_hidden(self, folder):
        record = folder
        while record:
            if not record.get("isVisible", True):
                warn(f"'{folder.get('name')}' is hidden in Compositor, so the layers just added to it don't show until "
                     f"it is shown again", once=True)
                return
            record = next((other for other in self.layers if other["id"] == record.get("parentID")), None)

    def put(self, key, records):
        """Adds `records` (bottom to top) where layers of group `key` go (GROUPS): on top of what is in the group's
        folder, which is made first when the project has none, or at the top level in the group's place for a group
        without a folder. A record with a parentID keeps it (the contents of a folder among `records`)."""
        if key not in FOLDER_NAMES:
            self._insert(self._top_level_index(key), records, None)
            return
        folder = self.group_folder(key)
        if folder is None:
            folder = self._group_folder(key, records)
            self._insert(self._top_level_index(key), [folder], None)
        else:
            self._warn_hidden(folder)
        self.insert_into(folder, records)

    def composite_before(self, record):
        """What Compositor draws under `record`."""
        drawn = []
        for other, _ in render_order(self.layers):
            if other["id"] == record["id"]:
                break
            if not other.get("isGroup"):
                drawn.append(other)
        return self.composite_through(drawn[-1]) if drawn else np.ones((self.height, self.width, 3), np.float32)

    def composite_under(self, key):
        """What Compositor draws under the place `put(key, ...)` adds records to: what a layer going there is computed
        from (the glow from all but the grain and the observation folder above it, say)."""
        folder = self.group_folder(key) if key in FOLDER_NAMES else None
        mark = {"id": "", "name": ""}  # a stand-in for the records, where they would go
        if folder is None:
            index = self._top_level_index(key)
        else:
            index = self._end_of(folder)
            mark["parentID"] = folder["id"]
        drawn = []
        for record, _ in render_order(self.layers[:index] + [mark] + self.layers[index:]):
            if record is mark:
                break
            if not record.get("isGroup"):
                drawn.append(record)
        if not drawn:
            return np.ones((self.height, self.width, 3), np.float32)
        if self._work is not None and drawn[-1]["id"] == self._work_anchor:  # right above the skin stack
            return self._work
        return self.composite_through(drawn[-1])

    # ---- writing ----

    def commit(self):
        """Everything this run made into the project, as one change for Compositor: it is checked first (the
        manifest by Compositor's load rules, against the files where they wait), then the files are renamed into
        images/ and the manifest over the old one within a few milliseconds, each rename atomic, so Compositor
        (which reloads a third of a second after the last change it sees) reloads once, with it all in place."""
        self._check_stamp()
        self.finish_saving()
        self.manifest["version"] = max(int(self.manifest.get("version") or 0), FORMAT_VERSION, needed_version(self.layers))
        if not self.manifest.get("activeLayerID") and self.added:
            self.manifest["activeLayerID"] = self.added[-1]["id"]
        validate_manifest(self.manifest, self.images, staged=self._staged)
        serialized = json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n"
        # Finder's Space-bar preview of the package: stale now (Compositor rewrites it on its next save); removed
        # before the burst, which Compositor counts from the last change it sees.
        shutil.rmtree(os.path.join(self.path, "QuickLook"), ignore_errors=True)
        swapped = []  # (layer image, the old one kept until the manifest is in) for recomputed layers
        placed = []   # new files put in images/
        try:
            for filename, new in self._staged.items():
                final = os.path.join(self.images, filename)
                if os.path.exists(final):
                    old = os.path.join(self.staging, f"{filename}.old")
                    os.replace(final, old)
                    swapped.append((final, old))
                else:
                    placed.append(final)
                os.replace(new, final)  # each file atomic: Compositor never reads half of one
            temporary = os.path.join(self.path, ".manifest.json.tmp")
            with open(temporary, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(temporary, os.path.join(self.path, "manifest.json"))  # atomic: never a half-written manifest
        except BaseException:
            for final, old in reversed(swapped):  # the layers' own pixels back
                os.replace(old, final)
            for final in placed:
                with contextlib.suppress(OSError):
                    os.remove(final)
            raise
        self._staged = {}
        self.written = []
        shutil.rmtree(self.staging, ignore_errors=True)  # the .old files with it
        if self.prune:
            referenced = {record.get(key) for record in self.layers for key in ("imageFile", "maskFile")}
            for name in os.listdir(self.images):
                if name.endswith((".png", ".exr")) and name not in referenced:
                    os.remove(os.path.join(self.images, name))

    def discard(self):
        """Undoes this run's file writes after a failure."""
        self.finish_saving(check=False)
        for path in self.written:
            try:
                os.remove(path)
            except OSError:
                pass
        shutil.rmtree(self.staging, ignore_errors=True)
        if self.created_dir:
            shutil.rmtree(self.path, ignore_errors=True)


# ==========================================
# 写入前校验 (Compositor's load rules, checked before the manifest is replaced)
# ==========================================

def pixel_budget():
    """Compositor's per-document raster budget on this Mac: a sixteenth of memory, 200-800 megapixels."""
    try:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        memory = 8 * 2 ** 30
    return min(800_000_000, max(200_000_000, memory // 16))


def png_header(path):
    with open(path, "rb") as f:
        head = f.read(33)
    if len(head) < 33 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    width, height = struct.unpack(">II", head[16:24])
    return width, height, head[24], head[25]  # bit depth, color type


def exr_size(path):
    """An OpenEXR file's width and height, from its header's data window."""
    with open(path, "rb") as f:
        head = f.read(65536)
    if head[:4] != b"\x76\x2f\x31\x01":
        raise ValueError("not an OpenEXR file")
    at = 8
    while at < len(head) and head[at]:  # attributes: name\0 type\0 size value, until an empty name
        name_end = head.index(b"\0", at)
        type_end = head.index(b"\0", name_end + 1)
        size, = struct.unpack("<i", head[type_end + 1:type_end + 5])
        if head[at:name_end] == b"dataWindow":
            x0, y0, x1, y1 = struct.unpack("<iiii", head[type_end + 5:type_end + 21])
            return x1 - x0 + 1, y1 - y0 + 1
        at = type_end + 5 + size
    raise ValueError("no data window in the OpenEXR header")


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def needed_version(layers):
    """The lowest format version that holds these layers' adjustments (KIND_VERSIONS, FILTER_BLOCK_VERSIONS); 1
    when nothing asks for more."""
    needed = 1
    for record in layers:
        settings = record.get("adjustment")
        if isinstance(settings, dict):
            needed = max(needed, KIND_VERSIONS.get(settings.get("kind"), 7))
            blocks = settings.get("filterLayerSettings")
            if isinstance(blocks, dict):
                needed = max(needed, 16, *(v for key, v in FILTER_BLOCK_VERSIONS.items() if blocks.get(key) is not None))
    return needed


def filter_layer_problems(blocks):
    """What Compositor would refuse in a filterLayerSettings object: it decodes every shared field (none may be left
    out) and requires each value to equal its clamped self (FilterLayerSettings.isValid)."""
    if not isinstance(blocks, dict):
        return ["filterLayerSettings must be an object"]
    problems = []
    for key, default in FILTER_SHARED.items():
        value = blocks.get(key)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                problems.append(f"filterLayerSettings.{key} must be true or false (every shared field is required)")
        elif not is_number(value) or not FILTER_SHARED_RANGES[key][0] <= value <= FILTER_SHARED_RANGES[key][1]:
            problems.append(f"filterLayerSettings.{key} must be a number from {FILTER_SHARED_RANGES[key][0]} to "
                            f"{FILTER_SHARED_RANGES[key][1]} (every shared field is required)")
    for name, ranges in (("tonal", TONAL_RANGES), ("sharpen", SHARPEN_RANGES), ("reduceNoise", REDUCE_NOISE_RANGES)):
        block = blocks.get(name)
        if block is None:
            continue
        if not isinstance(block, dict) or not all(is_number(block.get(key)) and low <= block[key] <= high
                                                  for key, (low, high) in ranges.items()):
            problems.append(f"filterLayerSettings.{name} needs " + ", ".join(
                f"{key} {low}-{high}" for key, (low, high) in ranges.items()))
    return problems


def adjustment_problems(settings):
    problems = []
    if settings.get("kind") not in ADJUSTMENT_KINDS:
        problems.append(f"unknown adjustment kind {settings.get('kind')!r}")
    for key in ("hue", "saturation", "lightness"):
        if not is_number(settings.get(key)):
            problems.append(f"adjustment '{key}' must be a number")
    if not isinstance(settings.get("colorize"), bool):
        problems.append("adjustment 'colorize' must be true or false")
    levels = settings.get("levels")
    if not isinstance(levels, dict) or levels.get("channel") not in CHANNELS or len(levels.get("ranges") or []) != 4 \
            or not all(isinstance(r, dict) and all(is_number(r.get(k)) for k in
                       ("black", "gamma", "white", "outputBlack", "outputWhite")) for r in levels["ranges"]):
        problems.append("adjustment needs a 'levels' block with four complete ranges")
    curve_set = settings.get("curves")
    channels = curve_set.get("channels") if isinstance(curve_set, dict) else None
    if not isinstance(channels, list) or len(channels) != 4 or curve_set.get("channel") not in CHANNELS:
        problems.append("adjustment needs a 'curves' block with four channels")
    else:
        for points in channels:
            xs = [p.get("x") for p in points] if isinstance(points, list) else []
            ys = [p.get("y") for p in points] if isinstance(points, list) else []
            if not (2 <= len(xs) <= 32) or xs[0] != 0 or xs[-1] != 255 \
                    or not all(is_number(v) and 0 <= v <= 255 for v in xs + ys) \
                    or any(a >= b for a, b in zip(xs, xs[1:])):
                problems.append("each curve needs 2-32 points from x 0 to x 255, increasing, values 0-255")
                break
    grain = settings.get("grainSettings")
    if grain is not None and not (
            is_number(grain.get("amount")) and 0 <= grain["amount"] <= 100
            and is_number(grain.get("size")) and 0.5 <= grain["size"] <= 20
            and is_number(grain.get("roughness")) and 0 <= grain["roughness"] <= 100
            and isinstance(grain.get("seed"), int) and 0 <= grain["seed"] < 2 ** 32):
        problems.append("grainSettings out of range")
    hsv = settings.get("hsvSettings")
    if hsv is not None:
        if not all(key in hsv for key in ("range", "colorize", "invertRange", "adjustments", "bands")) \
                or len(hsv.get("adjustments") or []) % 2 or len(hsv.get("bands") or []) % 2:
            problems.append("hsvSettings incomplete (adjustments and bands are flat [name, value, ...] arrays)")
    if settings.get("filterLayerSettings") is not None:
        problems.extend(filter_layer_problems(settings["filterLayerSettings"]))
    return problems


# Every field of each layer effect and its range (LayerEffects.swift). Compositor rejects the project when a field is
# missing, and loads but doesn't draw an effect with a value out of range. `enabled` may be left out (visible).
_COLOR = {"red": (0, 1), "green": (0, 1), "blue": (0, 1), "opacity": (0, 1)}
_CAST = {"angle": (-360, 360), "distance": (0, 5000), "blur": (0, 500), **_COLOR}
EFFECT_FIELDS = {
    "stroke": {"size": (0, 500), **_COLOR, "inside": bool}, "shadow": _CAST, "colorOverlay": _COLOR,
    "innerShadow": _CAST, "outerGlow": {"size": (0, 500), **_COLOR}, "innerGlow": {"size": (0, 500), **_COLOR},
}


def effect_problems(effects):
    if not isinstance(effects, dict):
        return ["effects must be an object"]
    problems = []
    for kind, fields in EFFECT_FIELDS.items():
        effect = effects.get(kind)
        if effect is None:
            continue
        if not isinstance(effect, dict):
            problems.append(f"effects.{kind} must be an object")
            continue
        if effect.get("enabled") is not None and not isinstance(effect["enabled"], bool):
            problems.append(f"effects.{kind}.enabled must be true or false")
        for key, rule in fields.items():
            value = effect.get(key)
            if rule is bool:
                if not isinstance(value, bool):
                    problems.append(f"effects.{kind}.{key} must be true or false")
            elif not (is_number(value) and rule[0] <= value <= rule[1]):
                problems.append(f"effects.{kind}.{key} must be a number from {rule[0]} to {rule[1]}")
    return problems


def validate_manifest(manifest, images_dir, staged=None):
    """The checks Compositor's ProjectStore makes when it loads a project. It rejects the whole project,
    without any message, when one fails, so failing here (before anything is replaced) is the useful place.
    `staged` maps the files a run wrote to where they wait, so a project is checked before they go in."""
    problems = []
    staged = staged or {}
    bad = problems.append
    if manifest.get("format") != FORMAT:
        bad("format must be com.compositor.project")
    version = manifest.get("version")
    if not isinstance(version, int) or version < 1:
        bad("version must be a positive integer")
        version = FORMAT_VERSION
    if manifest.get("colorSpace") != "sRGB":
        bad("colorSpace must be sRGB")
    for key in ("width", "height"):
        if not isinstance(manifest.get(key), int) or not 1 <= manifest[key] <= MAX_SIDE:
            bad(f"{key} must be 1-{MAX_SIDE}")
    if "resolution" in manifest and not (is_number(manifest["resolution"]) and 1 <= manifest["resolution"] <= 9600):
        bad("resolution must be 1-9600")
    layers = manifest.get("layers") or []
    if len(layers) > 10_000:
        bad("more than 10,000 layers")

    by_id = {}
    for record in layers:
        name = record.get("name")
        label = repr(name)
        if not UUID_PATTERN.match(str(record.get("id", ""))):
            bad(f"{label}: id is not a UUID")
            continue
        uid = record["id"].upper()
        if uid in by_id:
            bad(f"{label}: duplicate id")
        by_id[uid] = record
        if not isinstance(name, str) or not name.strip() or len(name.encode("utf-8")) > 16_384:
            bad(f"{label}: name must be non-empty text")
        if not isinstance(record.get("isVisible"), bool):
            bad(f"{label}: isVisible must be true or false")
        transform = record.get("transform")
        if not isinstance(transform, dict) or not all(key in transform for key in
                                                      ("origin", "size", "rotation", "flipX", "flipY", "sampling")):
            bad(f"{label}: transform needs origin, size, rotation, flipX, flipY and sampling")
        else:
            origin, size = transform["origin"], transform["size"]
            if not (isinstance(origin, list) and len(origin) == 2 and all(is_number(v) and abs(v) <= 1_000_000 for v in origin)
                    and isinstance(size, list) and len(size) == 2 and all(is_number(v) and 1 <= v <= 300_000 for v in size)
                    and is_number(transform["rotation"]) and isinstance(transform["flipX"], bool)
                    and isinstance(transform["flipY"], bool) and transform["sampling"] in SAMPLINGS):
                bad(f"{label}: transform origin/size must be [x, y] / [w, h] arrays with valid values")
        image_file, mask_file = record.get("imageFile"), record.get("maskFile")
        if image_file is not None and image_file != f"{uid}.png" and not (version >= 13 and image_file == f"{uid}.exr"):
            bad(f"{label}: imageFile must be '{uid}.png'" + (f" or '{uid}.exr'" if version >= 13 else ""))
        if mask_file is not None and mask_file != f"{uid}.mask.png":
            bad(f"{label}: maskFile must be '{uid}.mask.png'")
        if record.get("maskEnabled") is not None and mask_file is None:
            bad(f"{label}: maskEnabled without maskFile")
        opacity = record.get("opacity", 1)
        if not (is_number(opacity) and 0 <= opacity <= 1):
            bad(f"{label}: opacity must be 0-1")
        blend_mode = record.get("blendMode", "Normal")
        if blend_mode not in BLEND_MODES:
            bad(f"{label}: unknown blend mode {blend_mode!r}")
        if record.get("isGroup"):
            if image_file is not None or record.get("adjustment") is not None or blend_mode != "Normal":
                bad(f"{label}: a folder has no image or adjustment and blends Normal")
        if record.get("adjustment") is not None:
            if image_file is not None:
                bad(f"{label}: an adjustment layer has no imageFile")
            settings = record["adjustment"]
            problems.extend(f"{label}: {problem}" for problem in adjustment_problems(settings))
            needed = max(7, KIND_VERSIONS.get(settings.get("kind"), 7)) if isinstance(settings, dict) else 7
            filter_settings = settings.get("filterLayerSettings") if isinstance(settings, dict) else None
            if filter_settings is not None:
                needed = max(needed, 16, *(v for key, v in FILTER_BLOCK_VERSIONS.items()
                                           if isinstance(filter_settings, dict) and filter_settings.get(key) is not None))
            if version < needed:
                bad(f"{label}: this adjustment needs format version {needed} (the project is {version})")
        if record.get("effects") is not None:
            problems.extend(f"{label}: {problem}" for problem in effect_problems(record["effects"]))

    for record in layers:
        seen, parent = set(), record.get("parentID")
        while parent is not None:
            node = by_id.get(str(parent).upper())
            if node is None or not node.get("isGroup") or parent in seen or len(seen) > 64:
                bad(f"{record.get('name')!r}: parentID must name a folder, without cycles")
                break
            seen.add(parent)
            parent = node.get("parentID")
    active = manifest.get("activeLayerID")
    if active is not None and str(active).upper() not in by_id:
        bad("activeLayerID names no layer")

    budget = pixel_budget()
    used = {"image": 0, "mask": 0}
    for record in layers:
        for kind, filename in (("image", record.get("imageFile")), ("mask", record.get("maskFile"))):
            if not filename:
                continue
            path = staged.get(filename) or os.path.join(images_dir, filename)
            try:
                if filename.endswith(".exr"):  # an HDR layer: its depth is its own
                    (width, height), depth, color_type = exr_size(path), 8, None
                else:
                    width, height, depth, color_type = png_header(path)
            except (OSError, ValueError) as error:
                bad(f"{record.get('name')!r}: images/{filename} missing or unreadable ({error})")
                continue
            if width > MAX_SIDE or height > MAX_SIDE:
                bad(f"images/{filename}: at most {MAX_SIDE} px a side")
            if depth > 8 and not (depth == 16 and kind == "image" and version >= 12):
                bad(f"images/{filename}: must be 8-bit" + (" (16-bit from format version 12)" if kind == "image" else ""))
            if kind == "mask" and (color_type != 0 or depth != 8):
                bad(f"images/{filename}: a mask must be 8-bit grayscale without alpha")
            used[kind] += width * height
    for kind, pixels in used.items():
        if pixels > budget:
            bad(f"{kind} pixels total {pixels / 1e6:.0f} MP, over Compositor's {budget / 1e6:.0f} MP budget on this Mac")
    if problems:
        raise BuildError("Compositor would reject this project, so nothing was changed:\n  - " + "\n  - ".join(problems[:25]))


# ==========================================
# 精修模块 (Retouching modules)
# ==========================================


# The layers --refresh recomputes: pixel snapshots of what was drawn under them, which the user only edits through
# their masks, and which hide what is under them where they show (Normal, or Color for the color): how their names
# begin, what else their names say, their blend mode.
REFRESHED = (
    (SMOOTH, "保边磨皮", "Normal"),
    ("👁️", "眼神光", "Normal"),
)


def manifest_stamp(path):
    """manifest.json's (mtime in ns, size): whether it changed since the project was opened."""
    status = os.stat(path)
    return status.st_mtime_ns, status.st_size


def refreshed_kind(record):
    """Which of REFRESHED a record is: a pixel layer whose name begins with the emoji, in the blend mode the module
    uses. The rest of the name is the user's to change (SKILL.md tells them to keep the emoji)."""
    name = str(record.get("name", ""))
    if record.get("isGroup") or not record.get("imageFile") or record.get("adjustment") is not None:
        return None
    for begins, _, mode in REFRESHED:
        if name.startswith(begins) and (record.get("blendMode") or "Normal") == mode:
            return begins
    return None


def is_manual(name):
    """Whether a smoothing layer's name marks it as brushed on by hand: the ✋ after the 🔥 (kept when the user
    renames the layer, like the 🔥), or the words earlier versions put at the end."""
    return str(name).startswith(MANUAL_PREFIX) or MANUAL_MASK in str(name)


def skin_strength(args):
    """The strength the smoothing works at: --skin-strength, else --strength."""
    return args.strength if args.skin_strength is None else args.skin_strength


def existing(project, prefixes, says=None):
    """The project's records, anywhere in it, whose names begin with one of `prefixes` (and say `says`), bottom to
    top: what a module already made, so running it again adds nothing twice."""
    found = []
    for record, _ in render_order(project.layers):
        name = str(record.get("name", ""))
        if name.startswith(prefixes) and (says is None or says in name):
            found.append(record)
    return found


def whole_canvas(project, record):
    """Whether a layer still covers the canvas as made (not moved, resized, rotated or flipped)."""
    transform = record.get("transform") or {}
    return (list(transform.get("origin", [])), list(transform.get("size", []))) == ([0, 0], [project.width, project.height]) \
        and not transform.get("rotation") and not transform.get("flipX") and not transform.get("flipY")


def is_deep(project, record):
    """Whether a layer's image is a 16-bit PNG."""
    try:
        return png_header(project.file_path(record.get("imageFile") or ""))[2] == 16
    except (OSError, ValueError):
        return False


def deep_under(project, record=None):
    """The 16-bit photo (RGB 0-1, float32) when a layer in `record`'s place (or a new one on top of the skin
    stack) is computed from the photo alone, through the 16-bit frequency pair at most, which rebuilds it: then a
    16-bit project keeps its precision into the layer. None when other layers sit under it, or the photo is 8-bit."""
    photo = project.deep_base()
    if photo is None:
        return None
    base = project.base_record()
    by_id = {r["id"]: r for r in project.layers}
    if record is None:
        walk = {r["id"] for r in project.skin_walk()}

        def in_walk(r):
            while r is not None:
                if r["id"] in walk:
                    return True
                r = by_id.get(r.get("parentID"))
            return False
        under = [r for r, _ in render_order(project.layers) if not r.get("isGroup") and in_walk(r)]
    else:
        under = []
        for other, _ in render_order(project.layers):
            if other["id"] == record["id"]:
                break
            if not other.get("isGroup"):
                under.append(other)
    for other in under:
        if other["id"] == base["id"]:
            continue
        if not str(other.get("name", "")).startswith((FS_LOW, FS_HIGH)) or not is_deep(project, other):
            return None
    return photo


def stage_pixels(project, record, pixels):
    """New pixels for an existing layer, staged (see stage_png) at the layer's own depth: a 16-bit layer stays
    16-bit. `pixels`: an RGB image, 8-bit, or floats 0-1."""
    if isinstance(pixels, Image.Image):
        pixels = np.asarray(pixels.convert("RGB"))
    if is_deep(project, record):
        project.stage_deep_png(pixels.astype(np.float32) / 255.0 if pixels.dtype == np.uint8 else pixels, record["imageFile"])
    else:
        project.stage_png(Image.fromarray(pixels if pixels.dtype == np.uint8 else to8(pixels)).convert("RGBA"), record["imageFile"])


def remade(project, record, pixels, note, **fields):
    """A snapshot layer made again in place: new pixels (see stage_pixels), any changed fields (name, opacity), the
    same id, mask and place. Reported with `note`."""
    stage_pixels(project, record, pixels)
    record.update(fields)
    project.refreshed.append((record, note))
    project._work, project._work_anchor = None, None
    project.skin_changed = True


def refreshed_pixels(project, record, kind, below, args):
    """The new pixels (RGB, 8-bit, or floats 0-1 for a 16-bit smoothing layer) of a REFRESHED layer, from `below`
    (0-1), what Compositor draws under it now, made as the module made them: with the strength its name records
    (else --skin-strength / --strength), the smoothing's engine, texture and mask kind as its name says."""
    name = record["name"]
    strength = recorded_strength(name, skin_strength(args))
    if kind == SMOOTH:
        engine = next((key for key, label in SMOOTH_ENGINES.items() if label.split(" · ")[0] in name), "form")
        texture = next((key for key, (_, label) in TEXTURES.items() if label in name), "natural")
        photo = deep_under(project, record) if is_deep(project, record) else None
        return smoothing_pixels(project, below if photo is None else photo, engine, texture, strength,
                                manual=is_manual(name), deep=photo is not None)[0]
    return np.asarray(eye_pixels(project, below))


def module_refresh(project, args):
    """Recomputes the snapshot layers (REFRESHED) in place, bottom to top, each from what Compositor draws under it
    now: after manual work under them (on the frequency-separation layers, say) they would cover it with the old
    picture. A layer keeps its id, name, mask, opacity and place; only its pixels change. Layers the user paints on
    (neutral gray, the dual curves' masks), the sculpt and the glow are left as they are, and so
    are the healing, shine and color-evening layers of earlier versions."""
    by_id = {record["id"]: record for record in project.layers}
    canvas = np.ones((project.height, project.width, 3), np.float32)
    for record, _ in render_order(project.layers):
        if record.get("isGroup"):
            continue
        kind = refreshed_kind(record)
        if kind is not None:
            if not whole_canvas(project, record):
                warn(f"'{record.get('name')}' was moved or resized, so --refresh leaves it as it is")
            else:
                stage_pixels(project, record, refreshed_pixels(project, record, kind, canvas, args))
                project.refreshed.append((record, "重算"))
        canvas = project._draw(canvas, record, by_id)  # with its new pixels, for the layers above it
    if not project.refreshed:
        say("[SKIP] --refresh: no layer to recompute (磨皮, 眼神光)")
        return
    project._work, project._work_anchor = None, None
    project.skin_changed = True


def module_freq_sep(project, args):
    if project.frequency_pair():
        say("[SKIP] A frequency-separation pair already sits above the photo")
        return
    if args.blur_radius and args.blur_radius > 0:
        radius, sized = args.blur_radius, "手动"
    elif project.faces():
        radius, sized = max(1.5, round(project.face_width() / FS_FACE, 1)), "按脸宽"
    else:
        radius, sized = fs_radius(project.width, project.height), "按分辨率"
    folder = project.folder(FOLDER_NAMES["freq_sep"])
    names = (f"{FS_LOW} · 肤色与光影 (Low σ={radius:g}px · {sized})", f"{FS_HIGH} · 纹理与毛孔 (High · Linear Light)")
    photo = project.deep_base()
    if photo is not None:
        # A 16-bit photo gets a 16-bit pair: the opaque low layer would otherwise make all of it 8-bit again.
        low = cv2.GaussianBlur(photo, (0, 0), radius)
        high = np.clip((photo - low) * 0.5 + 0.5, 0, 1)
        low_record = project.deep_layer(names[0], low, parent=folder)
        high_record = project.deep_layer(names[1], high, blend="Linear Light", parent=folder)
    else:
        rgb = project.base_rgb()
        low = cv2.GaussianBlur(rgb, (0, 0), radius)
        # Linear Light adds 2 * high - 1, so storing half the difference rebuilds the photo to within one level
        # and never clips; the full difference would double the detail and clip at strong edges.
        high = np.rint((rgb.astype(np.float32) - low.astype(np.float32) + 255.0) * 0.5).astype(np.uint8)
        low_record = project.pixel_layer(names[0], Image.fromarray(low), parent=folder)
        high_record = project.pixel_layer(names[1], Image.fromarray(high), blend="Linear Light", parent=folder)
    # Right above the photo: the opaque low layer would otherwise hide every layer under it. The pair rebuilds
    # the photo, so what the skin stack is computed from doesn't change.
    base = project.base_record()
    project.insert_above(base, [folder, low_record, high_record])
    if project._work_anchor == base["id"]:
        project._work_anchor = high_record["id"]


def healed_spots(project, work, strength):
    """`work` (0-1) with its spots healed (see find_spots and heal), the healed area as a feathered 8-bit mask, the
    spots in image coordinates and whether the skin was taken for acne-prone; None when no spot was found. The
    whole face is searched when the segmentation model tells hair from skin, else only the inner face."""
    skin, _ = project.skin()
    faces = project.faces()
    face_width = project.face_width()
    box = crop_box(skin, face_width / 6.0)
    crop_faces = [shifted(face, -box[1].start, -box[0].start) for face in faces]
    relative = texture(project.base_rgb(), skin, faces)
    wide = project.parts() is not None and bool(landmark_faces(faces))
    spots, core, acne = find_spots(work[box], skin[box], crop_faces, face_width,
                                   None if relative is None else relative[box], strength, wide)
    if not spots:
        return None
    healed, region = heal(work[box], spots, core, face_width)
    out = work.copy()
    out[box] = healed
    mask = np.zeros(skin.shape, np.uint8)
    feather = max(1.0, float(np.median([spot[2] for spot in spots])) / 3.0)
    mask[box] = cv2.GaussianBlur(region, (0, 0), feather)
    return out, mask, [(x + box[1].start, y + box[0].start, r) for (x, y, r, _) in spots], acne


def strength_note(strength):
    """How a layer's name records a --strength other than 1, so --refresh recomputes the layer as it was made."""
    return "" if abs(strength - 1.0) < 1e-6 else f" · 力度 {strength:g}"


def recorded_strength(name, default):
    """The strength a layer's name records (strength_note), else `default`."""
    found = re.search(r"力度 ([0-9]+(?:\.[0-9]+)?)", name)
    return float(found.group(1)) if found else default


SMOOTH_ENGINES = {"form": "保形 · 淡化痘印", "guided": "导向滤波", "bilateral": "双边滤波"}  # as layer names say them


def smoothing_pixels(project, work, engine, texture, strength, blur_radius=None, manual=False, deep=False):
    """The smoothing layer's pixels made from `work` (0-1): 8-bit, or floats 0-1 when `deep` (a 16-bit layer from
    the 16-bit photo), and how its name tells of the spots healed first. `manual`: for a layer brushed on by hand,
    computed over all the people's skin (Project.skin_reach), not the face alone."""
    mask, _ = project.skin()
    faces = project.faces()
    cleared = ""
    if engine == "form" and faces:
        # Smoothing only fades a blemish; its own structure (a whitehead, a dark core, a bump's glint) stays. So the
        # form engine first heals the spots it can find (on acne-prone skin, nearly all of them; see find_spots),
        # as DR5's Delicious Skin brush claims to, then smooths.
        found = healed_spots(project, work, strength)
        if found is not None:
            work, _, spots, acne = found
            project.spots = project.spots + spots
            cleared = f" · 祛痘 {len(spots)} 处{'(痘痘肌)' if acne else ''}"
    work8 = np.ascontiguousarray(work, np.float32) if deep else to8(work)
    reach = project.skin_reach() if manual else None
    fraction, _ = TEXTURES[texture]
    # Sized to the face (without one, to a quarter of the short side): the same 24 MP frame can hold a headshot or a
    # half-length portrait.
    face_width = project.face_width()
    if engine == "form":
        sigma, radius = max(1.5, face_width / fraction), None
    elif faces:
        sigma, radius = max(1.5, face_width / fraction), max(4.0, face_width / 30.0)
    else:
        sigma = fs_radius(project.width, project.height) * 150.0 / fraction
        radius = max(10.0, min(project.width, project.height) / 100.0)
    if blur_radius and blur_radius > 0:
        sigma = blur_radius
    if engine == "form":
        return smooth_skin_form(work8, mask, faces, face_width, sigma, reach=reach), cleared
    return smooth_skin(work8, engine, sigma, radius), cleared


MANUAL_MASK = "黑蒙版 · 画笔涂白处磨皮"  # how a manually brushed smoothing layer's name ends
MANUAL_PREFIX = f"{SMOOTH}✋"           # ...and begins, so the kind survives a rename that keeps the emoji


def smoothing_name(args, cleared, mask_label, strength, manual=False):
    return (f"{MANUAL_PREFIX if manual else SMOOTH} [DR5] 保边磨皮 · {SMOOTH_ENGINES[args.engine]}{cleared} · "
            f"{TEXTURES[args.texture][1]}{strength_note(strength)} ({mask_label})")


def module_delicious_skin(project, args):
    """The smoothing layer; run again on a project that has one of the same kind (skin mask, or brushed by hand),
    that layer is made again in place with this run's settings, its mask kept: a second click doesn't stack a
    second layer, and a new strength or texture takes effect."""
    manual = args.delicious_skin == "manual"
    strength = skin_strength(args)
    opacity = round(min(1.0, 0.88 * strength), 2)
    same = [r for r in project.layers if refreshed_kind(r) == SMOOTH and is_manual(r["name"]) == manual
            and whole_canvas(project, r)]
    if same:
        record = same[-1]
        photo = deep_under(project, record) if is_deep(project, record) else None
        smoothed, cleared = smoothing_pixels(project, project.composite_before(record) if photo is None else photo,
                                             args.engine, args.texture, strength, args.blur_radius, manual=manual,
                                             deep=photo is not None)
        label = MANUAL_MASK if manual else project.skin()[1]
        remade(project, record, smoothed, "重做 · 保留蒙版 · 要换自动/手动蒙版先删掉这层",
               name=smoothing_name(args, cleared, label, strength, manual) + (" · 16 位" if is_deep(project, record) else ""),
               opacity=opacity)
        return
    mask, mask_label = project.skin()
    # A 16-bit photo (with its 16-bit frequency pair at most under the new layer) gets a 16-bit smoothing layer, so
    # heavy grading over the skin later finds no 8-bit steps in it.
    photo = deep_under(project)
    smoothed, cleared = smoothing_pixels(project, project.work() if photo is None else photo, args.engine, args.texture,
                                         strength, args.blur_radius, manual=manual, deep=photo is not None)
    if manual:  # DR5's manual smoothing: brushed on where it's wanted
        layer_mask, alpha, mask_label = solid_mask(0), np.zeros(mask.shape, np.float32), MANUAL_MASK
    else:
        layer_mask, alpha = Image.fromarray(mask), mask.astype(np.float32) / 255.0 * opacity
    name = smoothing_name(args, cleared, mask_label, strength, manual)
    if photo is not None:
        record = project.deep_layer(name + " · 16 位", smoothed, opacity=opacity, mask=layer_mask)
        rgb = smoothed
    else:
        record = project.pixel_layer(name, Image.fromarray(smoothed), opacity=opacity, mask=layer_mask)
        rgb = smoothed.astype(np.float32) / 255.0
    project.stack_skin_layer(record, rgb, alpha, "Normal")


# The live sculpt: Compositor's Tonal Contrast at this amount with its default tone weights, through the half-strength
# sculpt mask, lifts and deepens the face's planes as much as the Soft Light pixel layer did (planes' luminance change
# 0.90-1.02 of it, correlation 0.95-0.99 on three test portraits, tests/bench.py sculpt). Being a high-pass from
# `radius` rather than a band-pass, it also raises the fine texture under the mask: by about 7% of the texture
# already there (the pixel layer: about 4%).
SCULPT_AMOUNT = 40
EYE_SHARPEN = 180  # percent, as the pixel layer's unsharp mask


def pixel_fallback(project, kind, what):
    version = int(project.manifest.get("version") or 0)
    say(f"[OK] {what} is a pixel layer: the project is format version {version} and {kind} adjustment layers need "
        f"{KIND_VERSIONS[kind]}. Saving it once in Compositor (1.5.x writes version 20, 1.6 {NEWEST_VERSION}) lets "
        f"the next run make a live one")


def sculpt_radius(project):
    """The sculpt's coarse scale: a twelfth of the largest face's width (planes of the face, not features), by the
    resolution without a face."""
    faces = project.faces()
    return max(4.0, (max(face[2] for face in faces) / 12.0) if faces else min(project.width, project.height) / 60.0)


def module_sculpt(project, args):
    if existing(project, "🗿"):
        say("[SKIP] --sculpt: the project has its sculpt layer; brush on its mask")
        return
    mask, how = project.painted("sculpt", sculpt_mask)
    coarse = sculpt_radius(project)
    if project.allows("Tonal Contrast"):
        # Live: follows whatever the user does under it, needs no --refresh, has no pixels for Compositor to decode.
        settings = tonal_contrast(amount=SCULPT_AMOUNT, radius=min(100.0, round(coarse, 1)))
        project.put("light", [project.adjustment_layer(f"🗿 [DR5] 3D 骨相立体感 (Tonal Contrast · 实时 · {how})",
                                                        settings, mask=mask)])
        return
    pixel_fallback(project, "Tonal Contrast", "the sculpt")
    rgb = to8(project.work())
    fine = fs_radius(project.width, project.height)
    luma8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    # Band-pass: the planes of the face (cheekbones, brow, jaw), not pores or noise.
    band = cv2.GaussianBlur(luma8, (0, 0), fine) - gaussian(luma8, coarse)
    gray = np.clip(np.rint(128.0 + 2.0 * band), 0, 255).astype(np.uint8)
    project.put("light", [project.pixel_layer(f"🗿 [DR5] 3D 骨相立体感 (中频反差 · 柔光 · {how})", Image.fromarray(gray),
                                              blend="Soft Light", opacity=0.6, mask=mask)])


def module_neutral_gray(project, args):
    if existing(project, "🩶"):
        say("[SKIP] --neutral-gray: the project has its neutral gray layer; paint on that one")
        return
    gray = Image.new("RGBA", (project.width, project.height), (128, 128, 128, 255))
    project.put("light", [project.pixel_layer("🩶 中性灰 D&B (50% 灰 · 柔光 · 画笔涂亮/涂暗)", gray, blend="Soft Light")])


def module_dual_curves(project, args):
    """The pair, or whichever of the two the project is missing (a second click adds nothing twice)."""
    records = []
    if not existing(project, "🌑 双曲线"):
        records.append(project.adjustment_layer("🌑 双曲线 · 压暗 (曲线 · 明度 · 黑蒙版涂白)", curves(BURN_CURVE),
                                                blend="Luminosity", mask=solid_mask(0)))
    if not existing(project, "☀️ 双曲线"):
        records.append(project.adjustment_layer("☀️ 双曲线 · 提亮 (曲线 · 明度 · 黑蒙版涂白)", curves(DODGE_CURVE),
                                                blend="Luminosity", mask=solid_mask(0)))
    if not records:
        say("[SKIP] --dual-curves: the project has the pair; brush on their masks")
        return
    project.put("light", records)


def eye_radius(project):
    """The unsharp mask's radius for the eyes, by the resolution."""
    return max(2.0, round(min(project.width, project.height) / 1500.0, 1))


def eye_pixels(project, work):
    """The eye-brilliance pixel layer made from `work` (0-1): sharpened, with more contrast."""
    photo = Image.fromarray(to8(work))
    enhanced = photo.filter(ImageFilter.UnsharpMask(radius=eye_radius(project), percent=EYE_SHARPEN, threshold=2))
    return ImageOps.autocontrast(enhanced, cutoff=1, preserve_tone=True)  # one stretch for all channels: no color cast


def module_eyes(project, args):
    """The eye-brilliance layer: live (a Sharpen adjustment through the iris mask) when the format allows, else a
    pixel snapshot. A project with the live one keeps it; one with the pixel layer gets that made again in place
    (mask, opacity, name kept)."""
    same = [r for r in project.layers if refreshed_kind(r) == "👁️" and whole_canvas(project, r)]
    if same:
        record = same[-1]
        remade(project, record, eye_pixels(project, project.composite_before(record)), "重做 · 保留蒙版")
        return
    if any((r.get("adjustment") or {}).get("kind") == "Sharpen" for r in existing(project, "👁️")):
        say("[SKIP] --eyes: the project has its live eye-brilliance layer; set its opacity or brush on its mask")
        return
    mask, how = project.painted("eyes", iris_mask)
    if project.allows("Sharpen"):
        settings = sharpen(amount=EYE_SHARPEN, radius=min(20.0, eye_radius(project)))
        project.put("features", [project.adjustment_layer(f"👁️ [DR5] 眼神光 · 虹膜锐化 (Sharpen · 实时 · {how})",
                                                           settings, opacity=0.7, mask=mask)])
        return
    pixel_fallback(project, "Sharpen", "the eye brilliance")
    project.put("features", [project.pixel_layer(f"👁️ [DR5] 眼神光 · 虹膜锐化 ({how})", eye_pixels(project, project.work()),
                                                 opacity=0.7, mask=mask)])


def _balance(shadows=(0, 0, 0), midtones=(0, 0, 0), highlights=(0, 0, 0)):
    """Color Balance settings from (cyan-red, magenta-green, yellow-blue) per tonal range, -100 to 100."""
    keys = ("CyanRed", "MagentaGreen", "YellowBlue")
    settings = {"preserveLuminosity": True}
    for prefix, values in (("shadow", shadows), ("mid", midtones), ("highlight", highlights)):
        settings.update({prefix + key: value for key, value in zip(keys, values)})
    return adjustment("Color Balance", colorBalanceSettings=settings)


def _channel_curves(rgb, red=None, green=None, blue=None):
    """A Curves adjustment with points on the RGB curve and, where given, on the red, green and blue ones."""
    record = curves(rgb)
    for index, points in ((1, red), (2, green), (3, blue)):
        if points:
            record["curves"]["channels"][index] = [{"x": x, "y": y} for x, y in points]
    return record


# Color grades: each a folder of live adjustment layers, (name, settings, what the layer does).
LOOKS = {
    "airy": ("日系清透", [
        ("提亮 · 降低反差 (曲线)", _channel_curves([(0, 22), (64, 86), (128, 152), (192, 212), (255, 250)]),
         "lifted, airy tones"),
        ("青蓝通透 (色彩平衡)", _balance(shadows=(-8, 4, 10), midtones=(-4, 2, 5), highlights=(0, 0, 3)), "a clean cyan cast"),
        ("淡雅 (色相/饱和度)", hue_saturation("Master", {"Master": (0, -18, 0), "Greens": (12, -10, 5)}), "quieter color")]),
    "film": ("胶片暖调", [
        ("褪色黑 · 柔和 S 曲线 (曲线 · 蓝通道)", _channel_curves(
            [(0, 26), (64, 66), (128, 130), (192, 200), (255, 242)], blue=[(0, 34), (128, 124), (255, 222)]),
         "faded blacks, warm highlights"),
        ("暖高光 · 青暗部 (色彩平衡)", _balance(shadows=(-6, 0, 6), highlights=(6, 0, -10)), "warm light, cool shade"),
        ("胶片色 (色相/饱和度)", hue_saturation("Master", {"Master": (0, -12, 0), "Greens": (-15, -20, 0)}), "film colors")]),
    "cool-fair": ("冷白皮", [
        ("提亮中间调 (曲线)", _channel_curves([(0, 0), (128, 144), (255, 255)]), "brighter skin"),
        ("去黄 · 提亮肤色 (色相/饱和度)", hue_saturation("Yellows", {"Yellows": (0, -35, 10), "Reds": (0, -10, 5),
                                                                "Master": (0, -5, 0)}), "less yellow"),
        ("冷调粉白 (色彩平衡)", _balance(midtones=(-3, -4, 8), highlights=(0, -2, 6)), "a cool, pink-white cast")]),
    "creamy": ("奶油肌", [
        ("柔和反差 (曲线)", _channel_curves([(0, 12), (64, 72), (128, 138), (192, 200), (255, 248)]), "soft contrast"),
        ("奶油暖调 (色彩平衡)", _balance(midtones=(4, -2, -5), highlights=(6, 0, -10)), "creamy warmth"),
        ("柔和肤色 (色相/饱和度)", hue_saturation("Yellows", {"Yellows": (0, -15, 6), "Reds": (0, -10, 0),
                                                         "Master": (0, -8, 0)}), "gentle skin color")]),
    "hk": ("港风", [
        ("对比 · 暖红 (曲线 · 红通道)", _channel_curves([(0, 0), (64, 52), (128, 128), (192, 206), (255, 255)],
                                                      red=[(0, 0), (128, 134), (255, 255)]), "contrast, warm reds"),
        ("青绿暗部 · 暖高光 (色彩平衡)", _balance(shadows=(-12, 10, 6), highlights=(8, 0, -12)), "teal shade, warm light"),
        ("浓郁色彩 (色相/饱和度)", hue_saturation("Master", {"Master": (0, 10, 0), "Reds": (0, 5, 0), "Greens": (-20, -10, 0)}),
         "rich color")]),
    "bw": ("黑白", [
        ("黑白 (Black & White)", adjustment("Black & White", blackWhiteSettings={
            "reds": 40, "yellows": 60, "greens": 40, "cyans": 60, "blues": 20, "magentas": 80,
            "tint": False, "tintHue": 40, "tintSaturation": 20}), "black and white"),
        ("加强反差 (曲线)", _channel_curves([(0, 0), (64, 52), (192, 210), (255, 255)]), "contrast")]),
}
def module_look(project, args):
    label, layers = LOOKS[args.look]
    if existing(project, f"{LOOK_PREFIX} 调色 · {label}"):
        say(f"[SKIP] --look {args.look}: the project has this grade; its folder's opacity sets the strength")
        return
    folder = project.folder(f"{LOOK_PREFIX} 调色 · {label} (文件夹不透明度调强弱 · 双击各层微调)")
    folder["opacity"] = round(min(1.0, args.strength), 2)
    children = [project.adjustment_layer(name, settings, parent=folder) for name, settings, _ in layers]
    project.put("look", [folder] + children)
    # look.jpg: the frame just under the grade, and with it.
    order = [record for record, _ in render_order(project.layers) if not record.get("isGroup")]
    below = order[order.index(children[0]) - 1]
    by_id = {record["id"]: record for record in project.layers}
    before = project.composite_through(below)
    after = before
    for child in children:
        after = project._draw(after, child, by_id)
    project.look_check = (to8(before), to8(after))


DREAM_MODE = "Screen"


def _glow_mask(project, colors):
    """White, so glow can be painted out of eyes or lips without making a mask first; with a cool color, darker over
    skin found next to a face. Returns the mask and how the layer name describes it."""
    if any(is_cool(color) for color in colors) and project.faces():
        skin, _ = project.skin()
        if skin.max() > 0:
            mask = to8(1.0 - (1.0 - GLOW_SKIN_KEEP) * skin.astype(np.float32) / 255.0)
            return Image.fromarray(mask), f"白蒙版可擦 · 冷色在皮肤处减到 {round(GLOW_SKIN_KEEP * 100)}%"
    return solid_mask(255), "白蒙版可擦"


def _overlay_note(color):
    return "颜色叠加已关, 打开可换色" if color == NATURAL else "颜色叠加可改色"


def module_glow(project, args):
    first = current = project.composite_under("glow")  # everything drawn under where the glow goes
    short = min(project.width, project.height)
    for style, colors in args.glow:
        if style == "dream":
            # Live: Compositor blurs whatever is below and blends it back, so it follows every later edit.
            radius = round(min(250.0, max(2.0, short / 100.0)), 1)
            opacity = round(min(1.0, 0.3 * args.strength), 2)
            records = [project.adjustment_layer(
                f"✨ 辉光 · 梦幻柔焦 (高斯模糊调整层 {radius}px · {'滤色' if DREAM_MODE == 'Screen' else '柔光'} · 双击改半径)",
                adjustment("Gaussian Blur", blurRadius=radius), blend=DREAM_MODE, opacity=opacity, mask=solid_mask(255))]
        else:
            look = GLOW_STYLES[style]
            opacity = round(min(1.0, look["opacity"] * args.strength), 2)
            rings = bloom(current, style, split=len(colors) == 2)
            mask, mask_note = _glow_mask(project, colors)
            title = f"✨ 辉光 · {look['label']}"
            if len(colors) == 1:
                records = [project.pixel_layer(
                    f"{title} · {color_label(colors[0])} (滤色 · {_overlay_note(colors[0])} · {mask_note})",
                    glow_image(rings[0]), blend="Screen", opacity=opacity, mask=mask, effects=color_overlay(colors[0]))]
            else:
                # Two colors: the ring hugging the highlights and the wide one, each with its own color overlay, in a
                # folder whose mask erases both at once.
                inner, outer = colors
                folder = project.folder(f"{title} · 双色 {color_label(inner)} / {color_label(outer)} "
                                        f"(内圈 + 外圈 · 文件夹{mask_note})")
                project._attach_mask(folder, mask)
                records = [folder] + [
                    project.pixel_layer(f"{ring} · {color_label(color)} (滤色 · {_overlay_note(color)})",
                                        glow_image(pixels), blend="Screen", opacity=opacity, parent=folder,
                                        effects=color_overlay(color))
                    for ring, color, pixels in (("外圈", outer, rings[1]), ("内圈", inner, rings[0]))]
        project.put("glow", records)
        current = project.composite_under("glow")  # the next glow starts from this one's light
    project.glow_check = (to8(first), to8(current))


def module_grain(project, args):
    if existing(project, GRAIN_PREFIX):
        say("[SKIP] --grain: the project has its grain layer; double-click it to adjust")
        return
    size = round(min(20.0, max(0.5, min(project.width, project.height) / 2000.0)), 2)
    settings = adjustment("Grain", grainSettings={"amount": 10, "size": size, "roughness": 50,
                                                  "seed": random.randrange(1, 2 ** 32)})
    project.put("grain", [project.adjustment_layer(f"{GRAIN_PREFIX} [DR5] 胶片颗粒 (Grain · {size}px · 双击调整)", settings)])


# The color observation folder: every pixel brought to this lightness, then each channel's curve takes the face's
# usual skin color to gray and spreads what differs from it by the gain (within the reach, in levels): skin as it
# usually is reads gray, redness red, a cast its own color.
OBSERVE_LEVEL, OBSERVE_GAIN, OBSERVE_REACH = 160, 3.0, 24
# The black & white observation folder: Photoshop's default mix, then a curve that puts this face's skin at middle
# gray with twice the contrast around it (a fixed curve flattened fair, high-key faces and crushed dark skin).
OBSERVE_BW = {"reds": 40, "yellows": 60, "greens": 40, "cyans": 60, "blues": 20, "magentas": 80,
              "tint": False, "tintHue": 40, "tintSaturation": 20}
OBSERVE_CONTRAST = 2.0


def observe_curves(project):
    """The observation folders' curves, calibrated on what Compositor draws under them (the whole stack below
    their place, color grades included, so a grade added before them doesn't throw them off): for the color
    folder, lightness evened out (for Luminosity) and per channel the face's usual skin color (the median over the
    skin, or over the whole frame without a face) to gray with differences spread out; for the black & white
    folder, a contrast curve centered on the skin's black & white value. Returns (level, spread, contrast, center)."""
    level = curves([(0, OBSERVE_LEVEL), (255, OBSERVE_LEVEL)])
    rgb = project.composite_under("inspection")[::4, ::4]  # a median doesn't need every pixel
    skin = project.skin()[0][::4, ::4] > 200 if project.faces() else None
    sampled = skin is not None and skin.sum() > 100
    flat = blend(rgb, adjusted(rgb, level), np.ones(rgb.shape[:2], np.float32), "Luminosity") * 255.0
    center = np.median(flat[skin] if sampled else flat.reshape(-1, 3), axis=0)
    channels = []
    for c in center:
        low, high = max(1.0, c - OBSERVE_REACH), min(254.0, c + OBSERVE_REACH)
        channels.append([(0, 0), (round(low), round(np.clip(OBSERVE_LEVEL - OBSERVE_GAIN * (c - low), 1, 254))),
                         (round(high), round(np.clip(OBSERVE_LEVEL + OBSERVE_GAIN * (high - c), 1, 254))), (255, 255)])
    gray = _black_white(to8(rgb), OBSERVE_BW).astype(np.float32)
    gray = gray if gray.ndim == 2 else gray[..., 0]
    middle = float(np.median(gray[skin] if sampled else gray))
    reach = 50.0
    points = [(0, 0)]
    if middle - reach > 5:
        points.append((round(middle - reach), round(128 - OBSERVE_CONTRAST * reach)))
    points.append((round(np.clip(middle, 1, 254)), 128))
    if middle + reach < 250:
        points.append((round(middle + reach), round(128 + OBSERVE_CONTRAST * reach)))
    points.append((255, 255))
    return (level, _channel_curves([(0, 0), (255, 255)], red=channels[0], green=channels[1], blue=channels[2]),
            curves(points), round(middle))


def module_inspection(project, args):
    """Two hidden observation folders, each skipped if the project has it: black & white with more contrast, for light
    and dark patches; and color alone, for redness, casts and blotches (see observe_curves)."""
    have = [str(r.get("name", "")) for r in project.layers if r.get("isGroup")]
    records = []
    want_color = not any(name.startswith(f"{OBSERVATION_PREFIX} · 颜色") for name in have)
    want_bw = not any(name.startswith(f"{OBSERVATION_PREFIX} · 黑白") for name in have)
    if not (want_color or want_bw):
        say("[SKIP] The observation folders already exist")
        return
    level, spread, contrast, middle = observe_curves(project)
    if want_color:
        folder = project.folder(f"{OBSERVATION_PREFIX} · 颜色 (平均肤色显示为灰, 点亮眼睛检查泛红偏色, 交付前关闭)",
                                visible=False)
        records += [folder,
                    project.adjustment_layer("亮度拉平 (曲线 · 明度)", level, blend="Luminosity", parent=folder),
                    project.adjustment_layer(f"颜色差异 ×{OBSERVE_GAIN:g} (曲线 · 红绿蓝通道 · 以这张脸的肤色为灰)", spread,
                                             parent=folder)]
    if want_bw:
        folder = project.folder(f"{OBSERVATION_PREFIX} · 黑白 + 反差 (点亮眼睛检查明暗斑驳, 交付前关闭)", visible=False)
        records += [folder,
                    project.adjustment_layer("黑白 (Black & White)", adjustment("Black & White", blackWhiteSettings=OBSERVE_BW),
                                             parent=folder),
                    project.adjustment_layer(f"曲线 · 以这张脸的亮度 ({middle}) 为中灰 · 反差 ×{OBSERVE_CONTRAST:g}", contrast,
                                             parent=folder)]
    project.put("inspection", records)


# The order modules run in, bottom to top. Frequency separation goes right above the photo; the skin stack
# (heal, smoothing, shine, color) above that, each computed from the result below it; the rest in their
# groups' folders above it (GROUPS), under the observation folder.
MODULES = [
    ("refresh", module_refresh),  # first: what this run adds builds on the recomputed layers
    ("freq_sep", module_freq_sep),
    ("delicious_skin", module_delicious_skin),
    ("sculpt", module_sculpt),
    ("neutral_gray", module_neutral_gray),
    ("dual_curves", module_dual_curves),
    ("eyes", module_eyes),
    ("look", module_look),
    ("glow", module_glow),
    ("grain", module_grain),
    ("inspection", module_inspection),
]
PRESETS = {
    "daily": ["delicious_skin", "neutral_gray", "inspection"],  # the everyday flow: gray for light and shade, DR5 skin
    "dr5": ["freq_sep", "delicious_skin", "sculpt", "dual_curves", "eyes", "grain", "inspection"],
    "studio": ["freq_sep", "neutral_gray", "dual_curves", "inspection"],
}


# ==========================================
# 检查图 (Check images: what the automatic steps found and did, viewable without Compositor)
# ==========================================

def face_crop(project):
    """The faces with room around them (or the whole frame without faces): rows and columns to slice with."""
    faces = project.faces()
    if not faces:
        return slice(0, project.height), slice(0, project.width)
    x0 = min(f[0] for f in faces); y0 = min(f[1] for f in faces)
    x1 = max(f[0] + f[2] for f in faces); y1 = max(f[1] + f[3] for f in faces)
    pad_x, pad_y = (x1 - x0) * 0.25, (y1 - y0) * 0.25
    return (slice(max(0, int(y0 - pad_y)), min(project.height, int(y1 + pad_y * 1.6))),
            slice(max(0, int(x0 - pad_x)), min(project.width, int(x1 + pad_x))))


def _label_font(size):
    """A font with Chinese for the check images' labels (macOS system fonts), else Pillow's own."""
    for name in ("Hiragino Sans GB.ttc", "PingFang.ttc", "STHeiti Light.ttc"):
        try:
            return ImageFont.truetype(os.path.join("/System/Library/Fonts", name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def contact_sheet(rows, width=1600, gap=8, strip=52):
    """Rows of labelled images on one sheet: each row's images side by side in cells half `width` wide, a label
    above each, so an agent reads one picture instead of several (it is shown reduced to about 1500 px across)."""
    font = _label_font(36)
    cell = (width - gap) // 2
    blocks = []
    for cells in rows:
        scaled = [(label, cv2.resize(image, (cell, max(1, round(image.shape[0] * cell / image.shape[1]))),
                                     interpolation=cv2.INTER_AREA)) for label, image in cells]
        block = Image.new("RGB", (width, strip + max(image.shape[0] for _, image in scaled)), "white")
        draw = ImageDraw.Draw(block)
        for column, (label, image) in enumerate(scaled):
            draw.text((column * (cell + gap) + 12, 6), label, fill=(40, 40, 40), font=font)
            block.paste(Image.fromarray(image), (column * (cell + gap), strip))
        blocks.append(block)
    sheet = Image.new("RGB", (width, sum(block.height for block in blocks) + gap * (len(blocks) - 1)), "white")
    y = 0
    for block in blocks:
        sheet.paste(block, (0, y))
        y += block.height + gap
    return sheet


def write_checks(project, path):
    """The check images, overview.jpg first: the face before and after the skin stack, the skin mask and the
    pre-painted feature masks on one sheet (one Read for an agent), then each on its own, and the grade's and the
    glow's before/after frames when there are any."""
    folder = os.path.splitext(path)[0] + ".checks"
    os.makedirs(folder, exist_ok=True)
    written = []
    sheet = []

    # glow.jpg and look.jpg: the whole frame before and after, side by side.
    for name, check in (("glow.jpg", project.glow_check), ("look.jpg", project.look_check)):
        if not check:
            continue
        before, after = check
        fit = min(1.0, 900.0 / before.shape[0], 800.0 / before.shape[1])
        size = (max(1, round(before.shape[1] * fit)), max(1, round(before.shape[0] * fit)))
        pair = np.hstack([cv2.resize(before, size, interpolation=cv2.INTER_AREA), np.full((size[1], 8, 3), 255, np.uint8),
                          cv2.resize(after, size, interpolation=cv2.INTER_AREA)])
        written.append(os.path.join(folder, name))
        Image.fromarray(pair).save(written[-1], quality=90)
    if not (project.skin_changed or project.paintings or project.spots):
        return written

    base = project.base_rgb()
    scale = min(1.0, 1600.0 / max(project.width, project.height))

    def shrink(image):
        if scale >= 1.0:
            return image.copy()
        return cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))),
                          interpolation=cv2.INTER_AREA)

    faces = project.faces()

    # masks.jpg: skin mask in green, hair kept out of it in magenta, what automatic fixes keep away from outlined in
    # blue, faces in yellow, healed spots in red.
    overlay = shrink(base).astype(np.float32)
    skin, _ = project.skin()
    for mask, color in ((skin, (40, 220, 90)), (project.hair, (230, 60, 220))):
        if mask is not None:
            tint = shrink(mask).astype(np.float32)[..., None] / 255.0 * 0.4
            overlay = overlay * (1 - tint) + np.array(color, np.float32) * tint
    overlay = np.ascontiguousarray(np.clip(overlay, 0, 255).astype(np.uint8))
    line = max(1, round(2 * max(1.0, scale * max(project.width, project.height) / 1600.0)))
    for (x, y, w, h) in faces:
        cv2.rectangle(overlay, (round(x * scale), round(y * scale)), (round((x + w) * scale), round((y + h) * scale)),
                      (255, 220, 0), line)
    outlines, _ = cv2.findContours(shrink(feature_mask(base.shape[:2], faces)), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(overlay, outlines, -1, (60, 140, 255), line)
    for (x, y, r) in project.spots:
        cv2.circle(overlay, (round(x * scale), round(y * scale)), max(3, round(r * 1.6 * scale)), (255, 40, 40), line)
    written.append(os.path.join(folder, "masks.jpg"))
    Image.fromarray(overlay).save(written[-1], quality=88)

    # The faces (or the whole frame), for the close-ups below.
    box = face_crop(project)
    top, left = box[0].start, box[1].start
    crop = base[box]
    fit = min(1.0, 900.0 / crop.shape[0], 800.0 / crop.shape[1])
    size = (max(1, round(crop.shape[1] * fit)), max(1, round(crop.shape[0] * fit)))
    # The sheet's mask panel: the faces with more room around them (the neck and shoulders, where the mask should
    # stop), not the whole frame, so a face in a full-length shot stays large enough to judge.
    wide = []
    for extent, (rows, start) in zip(overlay.shape[:2], ((box[0], top), (box[1], left))):
        length = rows.stop - rows.start
        wide.append(slice(max(0, round((start - 0.4 * length) * scale)), min(extent, round((start + 1.4 * length) * scale))))
    sheet.append([("皮肤蒙版", np.ascontiguousarray(overlay[wide[0], wide[1]]))])

    # before_after.jpg: the skin stack's effect, side by side.
    if project.skin_changed:
        gap = np.full((size[1], 8, 3), 255, np.uint8)
        before, after = (cv2.resize(image, size, interpolation=cv2.INTER_AREA) for image in (crop, to8(project.work())[box]))
        pair = np.hstack([before, gap, after])
        written.append(os.path.join(folder, "before_after.jpg"))
        Image.fromarray(pair).save(written[-1], quality=90)
        sheet.insert(0, [("修前", before), ("修后", after)])

    # features.jpg: the masks painted in from the landmarks, tinted, with the landmarks as dots.
    if project.paintings:
        colors = {"eyes": (0, 230, 255), "sculpt": (255, 140, 0)}
        view = cv2.resize(crop, size, interpolation=cv2.INTER_AREA).astype(np.float32)
        for name, mask in project.paintings.items():
            tint = cv2.resize(mask[box], size, interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255.0 * 0.6
            view = view * (1 - tint) + np.array(colors.get(name, (255, 255, 255)), np.float32) * tint
        view = np.ascontiguousarray(np.clip(view, 0, 255).astype(np.uint8))
        for face in landmark_faces(faces):
            for points in face.marks.values():
                for (px, py) in points:
                    cv2.circle(view, (round((px - left) * fit), round((py - top) * fit)), 2, (255, 255, 255), -1)
        written.append(os.path.join(folder, "features.jpg"))
        Image.fromarray(view).save(written[-1], quality=90)
        sheet[-1].append(("五官预涂", view))
    if sheet:
        written.insert(0, os.path.join(folder, "overview.jpg"))
        contact_sheet(sheet).save(written[0], quality=85)
    return written


# ==========================================
# 命令行 (Command line)
# ==========================================

def describe(record):
    if record.get("isGroup"):
        kind = "folder"
    elif record.get("adjustment"):
        kind = f"adjustment:{record['adjustment']['kind']}"
    else:
        kind = "pixels"
    parts = [kind, record.get("blendMode", "Normal"), f"{round(record.get('opacity', 1) * 100)}%"]
    if record.get("maskFile"):
        parts.append("mask")
    overlay = (record.get("effects") or {}).get("colorOverlay") if isinstance(record.get("effects"), dict) else None
    if isinstance(overlay, dict):
        try:
            color = "#" + "".join(f"{round(overlay[key] * 255):02X}" for key in ("red", "green", "blue"))
        except (KeyError, TypeError, ValueError):
            color = "?"
        parts.append(f"color overlay {color}" + (" off" if overlay.get("enabled") is False else ""))
    if not record.get("isVisible", True):
        parts.append("hidden")
    return f"{record['name']}  [{', '.join(parts)}]"


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Build layered portrait-retouching projects for Compositor (.comp)")
    parser.add_argument("--comp", required=True, help="the .comp project folder to create or add to")
    parser.add_argument("--init", metavar="PHOTO", help="create the project from this photo (JPEG, PNG or TIFF)")
    parser.add_argument("--force", action="store_true", help="with --init, replace an existing project and its layers")
    parser.add_argument("--8bit", dest="eight_bit", action="store_true",
                        help="with --init, only what Compositor before 1.5 reads: a 16-bit photo becomes an 8-bit "
                             "layer (format 11), and the sculpt and eye brilliance are pixel layers rather than the "
                             "live adjustment layers of format 18 and 19")
    parser.add_argument("--preset", choices=sorted(PRESETS),
                        help="daily (smoothing, neutral gray, observation folders; new or existing project), or a whole "
                             "layout for a new project (needs --init): dr5 (frequency separation, smoothing, sculpt, "
                             "dual curves, eye brilliance, grain, observation folders) or studio (frequency "
                             "separation, neutral gray, dual curves, observation folders)")
    parser.add_argument("--engine", choices=["form", "guided", "bilateral"], default="form",
                        help="skin smoothing: form (default; keeps the face's form, fades round dark spots), guided "
                             "(the earlier engine) or bilateral")
    parser.add_argument("--texture", choices=sorted(TEXTURES), default="natural",
                        help="how much fine skin texture the smoothing keeps: natural (default, about as much as the "
                             "photo), soft (about DR5's), smooth (polished)")
    parser.add_argument("--blur-radius", type=float, default=None,
                        help="frequency-separation radius in pixels (default: 1/200 of the largest face's width, at "
                             "least 1.5; without a face, from the resolution)")
    parser.add_argument("--strength", type=float, default=1.0,
                        help="0.1-2: scales the glow's and a look's opacity, and the smoothing unless --skin-strength "
                             "is given (default 1)")
    parser.add_argument("--skin-strength", type=float, default=None,
                        help="0.1-2: the smoothing's own strength (its opacity, 0.88 at 1, and how readily it heals "
                             "spots first), apart from --strength")
    parser.add_argument("--refresh", action="store_true",
                        help="recompute the snapshot layers that hide what is under them (smoothing, eye brilliance) in "
                             "place, from the project as it is now: after manual work under them, on the "
                             "frequency-separation layers say. Masks, opacity and names stay; save in Compositor first")
    parser.add_argument("--freq-sep", action="store_true", help="low/high frequency pair above the photo")
    parser.add_argument("--delicious-skin", nargs="?", const="auto", choices=["auto", "manual"],
                        help="DR5-style texture-keeping skin smoothing (spots healed first): on the skin mask, or "
                             "`manual` with a black mask to brush it on where wanted (as DR5's manual smoothing)")
    parser.add_argument("--sculpt", action="store_true",
                        help="3D sculpt: a live Tonal Contrast layer through a mask painted on the face's planes "
                             "(format 18+; a mid-frequency contrast pixel layer in a project before that)")
    parser.add_argument("--neutral-gray", action="store_true", help="50%% gray Soft Light layer for dodge & burn")
    parser.add_argument("--dual-curves", action="store_true", help="brighten/darken Curves adjustments, black masks")
    parser.add_argument("--eyes", action="store_true",
                        help="eye brilliance: a live Sharpen layer through a mask painted on the irises (format 19+; "
                             "a sharpened pixel copy in a project before that)")
    parser.add_argument("--look", choices=sorted(LOOKS),
                        help="color grade, as a folder of live adjustment layers: airy (日系清透), film (胶片暖调), "
                             "cool-fair (冷白皮), creamy (奶油肌), hk (港风), bw (黑白); --strength sets the folder's opacity")
    parser.add_argument("--glow", action="append", nargs="?", const=glow_spec("mist"), type=glow_spec,
                        metavar="STYLE[:COLOR[,COLOR]]",
                        help="glow layer, repeatable: mist (default; highlights bloom, shadows stay deep), halation "
                             "(film halo around highlights) or dream (live soft-focus Gaussian Blur adjustment). "
                             "COLOR: natural, " + ", ".join(GLOW_COLORS) + " or #RRGGBB, set as a color overlay that "
                             "can be changed in Compositor; two colors (inner,outer) make a two-tone glow")
    parser.add_argument("--no-parsing", action="store_true",
                        help="tell hair from skin by texture, without the optional segmentation model")
    parser.add_argument("--no-vision", action="store_true",
                        help="find faces with OpenCV instead of Apple Vision (no landmarks, masks start black)")
    parser.add_argument("--grain", action="store_true", help="film grain: Grain adjustment")
    parser.add_argument("--inspection", action="store_true",
                        help="hidden observation folders: black & white with more contrast, and color alone (the "
                             "lightness evened out, the color stronger)")
    parser.add_argument("--no-checks", action="store_true", help="don't write the <Name>.checks images")
    parser.add_argument("--render", nargs="?", const="", metavar="IMAGE",
                        help="also flatten the whole project as Compositor exports it (adjustment layers included) to "
                             "IMAGE (.jpg or .png; default <Name>.render.jpg next to the project), without opening it")
    parser.add_argument("--check", action="store_true",
                        help="only check an existing project against Compositor's load rules; changes nothing")
    parser.add_argument("--without", action="append", default=[], metavar="MODULE",
                        help="leave a module of the preset out, repeatable: --preset dr5 --without grain "
                             "--without sculpt")
    parser.add_argument("--progress", action="store_true",
                        help="also print a [STEP] line as each part starts (\"[STEP] 2/5 delicious_skin\", \"[STEP] render\", "
                             "\"[STEP] checks\"), for an app showing the build's progress")
    return parser.parse_args(argv)


def run(args):
    started = time.time()
    path = os.path.abspath(os.path.expanduser(args.comp)).rstrip(os.sep)
    if args.check:
        project = Project.open(path)
        validate_manifest(project.manifest, project.images)
        say(f"[OK] {path} passes Compositor's load checks ({len(project.layers)} layers). Stack, top to bottom:")
        for record, depth in render_order(project.layers, top_first=True):
            say(f"  {'    ' * depth}{describe(record)}")
        return
    if not 0.1 <= args.strength <= 2.0:
        raise BuildError("--strength must be between 0.1 and 2")
    if args.skin_strength is not None and not 0.1 <= args.skin_strength <= 2.0:
        raise BuildError("--skin-strength must be between 0.1 and 2")
    keys = {key for key, _ in MODULES}
    left_out = {name.strip().lstrip("-").replace("-", "_") for name in args.without}
    if left_out - keys:
        raise BuildError(f"--without: no module called {', '.join(sorted(left_out - keys))}")
    wanted = set(PRESETS.get(args.preset, [])) - left_out
    wanted.update(key for key, _ in MODULES if getattr(args, key))
    if args.preset in ("dr5", "studio") and not args.init:
        raise BuildError(f"--preset {args.preset} builds a new project: add --init <photo>")
    if args.force and not args.init:
        raise BuildError("--force only applies together with --init")
    if args.eight_bit and not args.init:
        raise BuildError("--8bit only applies together with --init")
    if not args.init and not wanted and args.render is None:
        raise BuildError("nothing to do: pass --init <photo>, --preset, a module flag or --render (see --help)")

    project = Project.create(path, args.init, args.force, deep=not args.eight_bit) if args.init else Project.open(path)
    project.use_vision = not args.no_vision
    project.use_parsing = not args.no_parsing
    try:
        steps = [(key, build) for key, build in MODULES if key in wanted]
        for number, (key, build) in enumerate(steps, 1):
            if args.progress:
                say(f"[STEP] {number}/{len(steps)} {key}")
            build(project, args)
        if args.init or wanted:  # a render alone only reads the project
            project.commit()
    except BaseException:
        project.discard()
        raise

    for record, done in project.refreshed:
        say(f"[OK] ~ {describe(record)} ({done})")
    for record in project.added:
        say(f"[OK] + {describe(record)}")
    if args.render is not None:
        if args.progress:
            say("[STEP] render")
        rendered = time.time()
        target = os.path.abspath(os.path.expanduser(args.render)) if args.render else os.path.splitext(path)[0] + ".render.jpg"
        if not target.lower().endswith((".jpg", ".jpeg", ".png")):
            target += ".jpg"
        image = Image.fromarray(project.render())
        image.save(target, **({"quality": 92} if target.lower().endswith((".jpg", ".jpeg")) else {}))
        say(f"[OK] Rendered {target} ({image.width}x{image.height}, {time.time() - rendered:.1f}s)")
    if (project.skin_changed or project.paintings or project.spots or project.glow_check or project.look_check) \
            and not args.no_checks:
        if args.progress:
            say("[STEP] checks")
        for check in write_checks(project, path):
            say(f"[OK] Check image: {check}")
    say(f"[SUCCESS] {path}: {len(project.layers)} layers, {time.time() - started:.1f}s")
    say("Stack, top to bottom:")
    for record, depth in render_order(project.layers, top_first=True):
        say(f"  {'    ' * depth}{describe(record)}")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # keep [OK] and [WARN] lines in order when piped
    args = parse_args(argv)
    try:
        run(args)
    except BuildError as error:
        say(f"[Error] {error}", sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
