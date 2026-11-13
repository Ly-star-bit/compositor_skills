#!/usr/bin/env python3
"""A first pass of dodge & burn, painted in PhotoCraft with its own brush on a neutral gray layer or on the masks of
two Curves layers, headless.

    pcraft_dodge_burn.py look  PHOTO                                   measure only: prints the numbers, writes look.jpg
    pcraft_dodge_burn.py apply PHOTO --needs skin=some,circles=clear,folds=none [--on gray|curves]
                                                                        paints: PHOTO_光影.jpg / .pcraft
PHOTO is a picture (JPEG, PNG, TIFF) or a layered document (.pcraft, .psd). The photo and every layer it has stay as
they were; the pass is new layers on top, in a new document, for the user to check, take back and paint on.

Everything PhotoCraft does goes through its MCP server (`photocraft-cli mcp`, see photocraft_mcp.py): no window opens.
PhotoCraft finds the faces (image.detectFaces), makes the layers (layer.new.layer with the Soft Light neutral fill, or
layer.newAdjustmentLayer.curves under a black mask) and paints (paint.stroke, a soft round brush). This script
measures the picture, decides where each stroke goes and how strong it is, and measures again what landed.

Twelve things can be painted, lightness only, on the face's skin, each as far as its need says (clear, some = half,
none; `auto` reads it off the measurement):
  patches evened out, by area:   forehead, cheeks, chin
  shade lifted:                  circles (under the eyes)
  creases lightened, a stroke along each line found:
                                 troughs (under the eye bags), folds (smile lines), marionette (below the mouth's
                                 corners), forehead_lines, frown (between the brows), crows (crow's feet)
  highlights lowered:            shine
  modelling added:               shape (never by auto)
What each is, where it is looked for and how it is judged is in references/regions.md; the why of every number in
references/method.md.
"""
import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))  # real path: the skill may be installed as a symlink
sys.path.insert(0, HERE)
from photocraft_mcp import Mcp, McpError, find_cli  # noqa: E402

try:
    import cv2
    import numpy as np
except ImportError as _error:  # said again, with the fix, when a command needs them
    cv2 = np = None
    _IMPORT_ERROR = _error

SUFFIX = "_光影"
MARK = "AI 光影"  # in the name of every layer this makes
DOCUMENTS = (".pcraft", ".psd", ".psb")  # layered: looked at as PhotoCraft renders them
EVEN = ("forehead", "cheeks", "chin")
CREASES = ("troughs", "folds", "marionette", "forehead_lines", "frown", "crows")
ITEMS = EVEN + ("circles",) + CREASES + ("shine", "shape")
# A word for several items at once in --needs.
GROUPS = {"skin": EVEN, "lines": ("forehead_lines", "frown", "crows", "marionette")}
LABELS = {"forehead": "额头不匀", "cheeks": "脸颊不匀", "chin": "下巴不匀", "circles": "黑眼圈", "troughs": "泪沟",
          "folds": "法令纹", "marionette": "嘴角纹", "forehead_lines": "抬头纹", "frown": "眉间纹", "crows": "鱼尾纹",
          "shine": "油光", "shape": "立体感"}
WORDS = {"forehead": "patches differ from the skin round them, forehead", "cheeks": "patches differ, cheeks",
         "chin": "patches differ, chin", "circles": "darker under the eyes than the skin round them",
         "troughs": "the line under the eye bags stands out", "folds": "the smile lines stand out",
         "marionette": "the lines below the mouth's corners stand out", "forehead_lines": "the forehead's lines stand out",
         "frown": "the lines between the brows stand out", "crows": "the crow's feet stand out",
         "shine": "the hot spots stand above the skin round them", "shape": "more modelling (only when asked for)"}
NEEDS = {"clear": 1.0, "some": 0.5, "none": 0.0}
NEED_LABELS = {"clear": "全量", "some": "一半", "none": "不修"}
KINDS = {"gray": "中性灰", "curves": "双曲线"}
# The share of what was measured that "clear" works with: a face evened out all the way is a mask.
FULL = dict({item: 0.9 for item in EVEN}, circles=0.5, shine=0.4, **{item: 0.5 for item in CREASES})
CAP = 0.06           # no stroke lifts or lowers the lightness by more than this (0-1): 15 levels
SHINE_CAP = 0.04     # ... and no shine is lowered by more than this: 10 levels (a highlight is also the face's form)
SHINE_FROM = 0.07    # shine is what stands this far above the skin round it (18 levels); less is the face's own light
EDGE = 0.05          # a patch further than this from its surroundings is a feature (a mole, a crease), not a blotch
STEP = (0.04, 0.10)  # where the lightness steps by this much over a patch's own width it is shape: left alone
CREASE = 0.10        # what a crease's lightening levels off at, at share 1: made shallower, not filled
# What `auto` makes of a measurement, in levels (0-255): under the first nothing, from it on half. From the second
# on the item is plain to see, and the table says that clear is worth considering: auto itself never takes clear.
# Starting values, set on public portraits (references/method.md has each one's numbers); not a client's verdict.
AUTO = dict({item: (2.0, 4.0) for item in EVEN}, circles=(4.0, 11.0), troughs=(5.0, 14.0), folds=(6.0, 20.0),
            marionette=(5.0, 14.0), forehead_lines=(5.0, 14.0), frown=(5.0, 14.0), crows=(5.0, 14.0), shine=(40.0, 55.0))
NOT_A_CREASE = 34.0  # a line darker than this beside the skin next to it is a hair, a lash or an edge, not a crease
NOT_CIRCLES = 22.0   # under the eyes, darker than this is eye makeup or a cast shadow, which auto leaves
SMALL_FACE = 70.0    # eyes closer than this many pixels: too few pixels of skin to measure, the face is left
# The two curves, as [in, out] points on the composite curve, and the brush as it was measured (method.md).
BURN_CURVE, DODGE_CURVE = [[0, 0], [128, 96], [255, 255]], [[0, 0], [128, 160], [255, 255]]
DAB_SIGMA = 0.235    # one dab of the soft round brush is a Gaussian this many brush sizes wide
DABS = {"forehead": 80, "cheeks": 110, "chin": 50, "shine": 40}  # the most dabs an item gets, the strongest first
# Modelling, at clear, in levels: where a retoucher's big soft brush adds light and shade to a face lit from the front.
SHAPE_LEVELS = {"nose bridge": 4.0, "forehead": 3.0, "cheek": 3.0, "chin": 3.0, "nose side": -3.0, "under cheekbone": -3.0}
_warned = []


class Fail(Exception):
    pass


def say(message):
    print(message, flush=True)


def warn(message, once=False):
    if once and message in _warned:
        return
    _warned.append(message)
    say(f"[WARN] {message}")


# ==========================================
# Pictures
# ==========================================

def need_libraries():
    if cv2 is None:
        raise Fail(f"{_IMPORT_ERROR}: python3 -m pip install \"opencv-python-headless<5\" numpy")


def read_raw(path, flags):
    """cv2.imread for any path (OpenCV's own takes only names in the system's encoding on Windows)."""
    try:
        return cv2.imdecode(np.fromfile(path, np.uint8), flags)
    except OSError:
        return None


def write_picture(path, image, quality=90):
    ok, data = cv2.imencode(os.path.splitext(path)[1].lower() or ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise Fail(f"could not write {path}")
    data.tofile(path)


def label(image, text, at=(8, 26), scale=0.7):
    text = "".join(c if 31 < ord(c) < 127 else "?" for c in text)  # OpenCV's built-in fonts are ASCII only
    wide = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0][0]
    scale = max(0.35, min(scale, scale * (image.shape[1] - 2 * at[0]) / max(1, wide)))  # it stays on its own picture
    cv2.putText(image, text, at, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, text, at, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), 2, cv2.LINE_AA)


def fit(image, side):
    """No longer than `side`; a small crop is shown up to twice its size, so a close-up is a close-up."""
    scale = min(2.0, side / max(image.shape[:2]))
    if abs(scale - 1.0) < 0.02:
        return image.copy()
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def side_by_side(tiles):
    height = max(tile.shape[0] for tile in tiles)
    padded = []
    for tile in tiles:
        if tile.shape[0] < height:
            tile = np.vstack([tile, np.zeros((height - tile.shape[0], tile.shape[1], 3), np.uint8)])
        padded += [tile, np.full((height, 6, 3), 255, np.uint8)]
    return np.hstack(padded[:-1])


def lum(bgr):
    """Lightness as the Luminosity blend mode reads it, from BGR floats 0-1."""
    return bgr[..., 2] * 0.3 + bgr[..., 1] * 0.59 + bgr[..., 0] * 0.11


def blur(image, sigma):
    """Gaussian blur; wide ones on a reduced copy (they only carry low frequencies)."""
    if sigma <= 8:
        return cv2.GaussianBlur(image, (0, 0), sigma)
    height, width = image.shape[:2]
    factor = max(2, int(sigma // 4))
    small = cv2.resize(image, (max(1, width // factor), max(1, height // factor)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / factor)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)


def masked_blur(image, weight, sigma):
    """A blur that averages only where `weight` is, so eyes, lips, hair and background don't bleed in."""
    support = blur(weight, sigma)
    blurred = blur(image * weight, sigma) / np.maximum(support, 1e-4)
    return np.where(support > 0.02, blurred, image)


def odd(value):
    return max(3, int(round(value)) | 1)


def ellipse(shape, center, axes, angle):
    mask = np.zeros(shape, np.uint8)
    cv2.ellipse(mask, (int(round(center[0])), int(round(center[1]))),
                (max(1, int(round(axes[0]))), max(1, int(round(axes[1])))), angle, 0, 360, 255, -1)
    return mask


def feathered(mask, sigma):
    return cv2.GaussianBlur(mask, (0, 0), max(1.0, sigma)).astype(np.float32) / 255.0


def at(image, points):
    """The image's values at `points` (N x 2, x then y), clamped to the picture."""
    rows = np.clip(points[:, 1].astype(int), 0, image.shape[0] - 1)
    columns = np.clip(points[:, 0].astype(int), 0, image.shape[1] - 1)
    return image[rows, columns]


# ==========================================
# The face: PhotoCraft's five landmarks, and the zones drawn from them
# ==========================================

class Face:
    """One face from image.detectFaces: the two eyes, the nose tip and the mouth's two corners. "right" is the one
    on the picture's left, as PhotoCraft names them."""

    def __init__(self, marks, score=1.0):
        try:
            self.eye_a, self.eye_b = (np.array(marks[name], np.float32) for name in ("rightEye", "leftEye"))
            self.nose = np.array(marks["nose"], np.float32)
            self.mouth_a, self.mouth_b = (np.array(marks[name], np.float32) for name in ("mouthRight", "mouthLeft"))
        except (KeyError, TypeError, ValueError):
            raise Fail("a face needs rightEye, leftEye, nose, mouthRight and mouthLeft, each [x, y] in picture pixels "
                       "(right = the one on the picture's left)") from None
        self.score = score
        self.apart = float(np.hypot(*(self.eye_b - self.eye_a)))  # between the eyes' centres
        if self.apart < 1:
            raise Fail("a face's two eyes are at the same place")
        self.eye = 0.45 * self.apart       # an eye, corner to corner
        self.width = 2.3 * self.apart      # the face
        self.across = (self.eye_b - self.eye_a) / self.apart
        self.down = np.array([-self.across[1], self.across[0]], np.float32)
        self.eyes = (self.eye_a + self.eye_b) / 2
        self.mouth = (self.mouth_a + self.mouth_b) / 2
        if float((self.mouth - self.eyes) @ self.down) < 0:
            self.down = -self.down
        self.tilt = math.degrees(math.atan2(self.across[1], self.across[0]))
        self.drop = float((self.mouth - self.eyes) @ self.down)  # eyes to mouth
        self.turned = float((self.nose - self.eyes) @ self.across)  # how far the nose tip sits off the midline
        self.base = self.nose - self.across * 0.5 * self.turned     # where the nose meets the face
        self.length = max(0.3 * self.apart, float((self.nose - self.eyes) @ self.down))  # eyes to nose tip

    def shifted(self, dx, dy):
        offset = [dx, dy]
        return Face({"rightEye": self.eye_a + offset, "leftEye": self.eye_b + offset, "nose": self.nose + offset,
                     "mouthRight": self.mouth_a + offset, "mouthLeft": self.mouth_b + offset}, self.score)

    def box(self, width, height):
        """The part of the picture this face is worked on in, as slices."""
        centre = self.eyes + self.down * 0.45 * self.drop
        reach = 1.25 * max(self.apart, 1.3 * self.drop)
        x0, y0 = max(0, int(centre[0] - reach)), max(0, int(centre[1] - reach))
        return slice(y0, min(height, int(centre[1] + reach))), slice(x0, min(width, int(centre[0] + reach)))


def zones(face, shape):
    """Everything placed by the landmarks, on an image of `shape` the face's coordinates are already in."""
    eye, apart, across, down, tilt = face.eye, face.apart, face.across, face.down, face.tilt
    out = {"eyes": []}
    guard = np.zeros(shape, np.uint8)
    bags = np.zeros(shape, np.uint8)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float32)
    below = (xx - face.eyes[0]) * down[0] + (yy - face.eyes[1]) * down[1]      # how far down from the eyes' line
    aside = (xx - face.eyes[0]) * across[0] + (yy - face.eyes[1]) * across[1]  # how far across from the midline
    crows = np.zeros(shape, np.uint8)
    for centre, side in ((face.eye_a, -1.0), (face.eye_b, 1.0)):
        opening = ellipse(shape, centre, (0.62 * eye, 0.30 * eye), tilt)
        crescent_centre = centre + down * 0.39 * eye
        crescent = ellipse(shape, crescent_centre, (0.55 * eye, 0.28 * eye), tilt)
        zone = crescent & ~opening
        around = odd(0.7 * eye)
        ring = cv2.dilate(crescent, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (around, around))) & ~crescent & ~opening
        ring[(xx - centre[0]) * down[0] + (yy - centre[1]) * down[1] < 0.1 * eye] = 0  # only what lies below the eye
        brow = ellipse(shape, centre - down * 0.55 * eye, (0.8 * eye, 0.24 * eye), tilt)
        guard |= opening | brow
        bags |= zone
        # The line under the eye bag is looked for a little lower and wider than the shade; the crow's feet beside
        # the eye's outer corner.
        trough = ellipse(shape, centre + down * 0.5 * eye, (0.62 * eye, 0.3 * eye), tilt) \
            & ~ellipse(shape, centre, (0.68 * eye, 0.36 * eye), tilt)
        corner = ellipse(shape, centre + across * side * 0.92 * eye + down * 0.08 * eye, (0.42 * eye, 0.5 * eye), tilt) \
            & ~ellipse(shape, centre, (0.66 * eye, 0.34 * eye), tilt) & ~brow
        crows |= corner
        out["eyes"].append(dict(zone=zone, ring=ring, opening=opening, centre=crescent_centre, trough=trough,
                                corner=corner, side=side))
    # The nose: its ridge and wings keep their own light. The mouth: lips are not skin.
    nose = ellipse(shape, face.eyes + across * 0.7 * face.turned + down * 0.55 * face.length, (0.26 * apart, 0.62 * face.length), tilt)
    nose |= ellipse(shape, face.base + down * 0.06 * apart, (0.36 * apart, 0.16 * apart), tilt)
    wide = float(np.hypot(*(face.mouth_b - face.mouth_a)))
    lips = ellipse(shape, face.mouth, (0.68 * wide, 0.36 * wide), tilt)
    # The smile lines: from beside the nostril to past the mouth's corner, bowing outwards. The lines below the
    # mouth's corners: looked for from each corner downwards.
    lines, curves, corners = np.zeros(shape, np.uint8), [], []
    for side, corner in ((-1.0, face.mouth_a), (1.0, face.mouth_b)):
        outward = across * side
        wing = face.base + outward * 0.27 * apart - down * 0.02 * apart
        start = wing + outward * 0.2 * eye + down * 0.05 * eye
        end = corner + outward * 0.3 * eye + down * 0.35 * eye
        bow = (start + end) / 2 + outward * 0.2 * eye
        curve = np.array([(1 - t) ** 2 * start + 2 * (1 - t) * t * bow + t ** 2 * end for t in np.linspace(0, 1, 24)])
        cv2.polylines(lines, [np.round(curve).astype(np.int32).reshape(-1, 1, 2)], False, 255, max(1, int(round(0.45 * eye))))
        curves.append((curve, outward))
        corners.append((ellipse(shape, corner + outward * 0.12 * eye + down * 0.62 * eye, (0.3 * eye, 0.42 * eye), tilt)
                        & ~lips, side))
    # (Wide enough for the temples, where the crow's feet are; what in it is skin is told by its colour.)
    oval = ellipse(shape, face.eyes + down * 0.45 * face.drop, (1.1 * apart, 1.3 * face.drop), tilt)
    # The three areas the skin's patches are measured and painted in, each on its own.
    areas = {"forehead": below < -0.62 * eye,
             "cheeks": (below >= -0.1 * eye) & (below < face.drop + 0.55 * wide) & (np.abs(aside - 0.5 * face.turned) > 0.2 * apart),
             "chin": below >= face.drop + 0.55 * wide}  # below the hollow under the lower lip, which is shape
    out.update(guard=guard | nose | lips, features=guard | lips, nose=nose, lips=lips, bags=feathered(bags, eye * 0.12),
               folds=feathered(lines, eye * 0.15), curves=curves, corners=corners, crows=crows, oval=oval, areas=areas,
               forehead=((below < -0.62 * eye) & (oval > 0)).astype(np.uint8) * 255,
               frown=ellipse(shape, face.eyes - down * 0.38 * eye, (0.3 * eye, 0.5 * eye), tilt))
    return out


def skin_colour(bgr, face, placed):
    """How far every pixel's colour is from the face's own cheeks (in deviations) and how much lighter or darker it
    is (L, 0-100): (distance, lightness), or None when the cheeks can't be read."""
    eye, down, shape = face.eye, face.down, bgr.shape[:2]
    soft = cv2.GaussianBlur(bgr, (0, 0), max(1.0, face.apart / 150.0)).astype(np.float32) / 255.0
    lab = cv2.cvtColor(soft, cv2.COLOR_BGR2Lab)  # L 0-100, a and b about -100 to 100
    sample = np.zeros(shape, np.uint8)
    for centre, side in ((face.eye_a, -1.0), (face.eye_b, 1.0)):
        sample |= ellipse(shape, centre + down * 1.05 * eye + face.across * side * 0.1 * eye, (0.42 * eye, 0.3 * eye), face.tilt)
    values = lab[(sample > 0) & (placed["guard"] == 0)]
    if len(values) < 50:
        return None
    centre = np.median(values, axis=0)
    spread = 1.4826 * np.median(np.abs(values - centre), axis=0) + 1e-3
    values = values[(np.abs(values - centre) < 3 * spread).all(axis=1)]
    if len(values) < 30:
        return None
    inverse = np.linalg.inv(np.cov(values[:, 1:].T) + np.eye(2) * 0.25)
    offset = lab[..., 1:] - centre[1:]
    return np.sqrt(np.einsum("...i,ij,...j->...", offset, inverse, offset)), lab[..., 0] - centre[0]


def skin_weight(colour, face, placed, without):
    """Where the face's skin is, 0-1: the colour of its own cheeks (within 3.5 deviations, from deep in shadow up
    to its highlights), inside the face's oval, without the features in `without`."""
    if colour is None:
        return np.zeros(placed["oval"].shape, np.float32)
    distance, lightness = colour
    skin = ((distance < 3.5) & (lightness > -45) & (lightness < 30) & (placed["oval"] > 0)).astype(np.uint8) * 255
    close = odd(face.apart / 40.0)
    skin = cv2.morphologyEx(skin, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    skin = cv2.morphologyEx(skin, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close, close)))
    skin[without > 0] = 0
    return feathered(skin, face.apart / 60.0)


def stands_out(depth, weight, points, eye):
    """How much darker a line is than the skin a little to either side of it, at each of its points (0-1). A crease
    is a line: the cheek's own shading, as dark to one side as on the line, reads as nothing."""
    tangent = np.gradient(points, axis=0)
    tangent /= np.maximum(np.hypot(tangent[:, 0], tangent[:, 1])[:, None], 1e-6)
    normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
    # The skin beside it: on each side the lightest of three places a little way off, so that a second line close by
    # (crow's feet lie a few millimetres apart) is not taken for the skin.
    beside = sum(np.minimum.reduce([at(depth, points + normal * side * far * eye) for far in (0.09, 0.14, 0.2)])
                 for side in (-1.0, 1.0)) / 2
    return np.clip(at(depth, points) - beside, 0, None) * (at(weight, points) > 0.3)


def in_skin(weight, points, eye, sides=True):
    """Whether a line runs through skin: skin on it and, with `sides`, close to both sides of it, most of the way.
    A hair across the face's edge, the end of a brow or a nostril's rim has skin on one side only."""
    inside = at(weight, points) > 0.5
    if sides:
        tangent = np.gradient(points, axis=0)
        tangent /= np.maximum(np.hypot(tangent[:, 0], tangent[:, 1])[:, None], 1e-6)
        normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
        inside &= (at(weight, points + normal * 0.12 * eye) > 0.5) & (at(weight, points - normal * 0.12 * eye) > 0.5)
    return float(inside.mean()) >= 0.7


def a_crease(weight, points, stands, eye, sides=True):
    """A line dark enough to be seen and not too dark to be skin, running through skin."""
    level = 255.0 * float(np.median(stands))
    return 2.0 <= level <= NOT_A_CREASE and in_skin(weight, points, eye, sides)


def find_creases(y, depth, weight, around, zone, eye, prefer, tolerance, most, shortest, crowded=None):
    """The dark lines in `zone` that run within `tolerance` degrees of the direction `prefer`, longest and darkest
    first, at most `most`: each as a line of points along its middle. A line is where the lightness curves upward
    across it (the larger eigenvalue of its second derivatives, at the scale of a crease), on skin. `weight` is the
    skin a crease is measured on, `around` the skin a line must have on both its sides (the nose's counts). With
    `crowded`, a zone where more than that share of the skin reads as lines is left: that is a fringe of hair over
    a forehead, not a forehead's lines."""
    sigma = max(1.2, 0.035 * eye)
    smooth = cv2.GaussianBlur(y, (0, 0), sigma)
    xx = cv2.Sobel(smooth, cv2.CV_32F, 2, 0, ksize=3) / 4.0
    yy = cv2.Sobel(smooth, cv2.CV_32F, 0, 2, ksize=3) / 4.0
    xy = cv2.Sobel(smooth, cv2.CV_32F, 1, 1, ksize=3) / 4.0
    root = np.sqrt(((xx - yy) / 2) ** 2 + xy ** 2)
    ridge = ((xx + yy) / 2 + root) * sigma * sigma           # about a third of the line's depth
    turn = 0.5 * np.arctan2(2 * xy, xx - yy)                 # the direction the lightness curves most in: across the line
    # The line's own direction, as an angle from `prefer` (-90 to 90 degrees).
    ahead = -np.sin(turn) * prefer[0] + np.cos(turn) * prefer[1]
    aside = -np.sin(turn) * -prefer[1] + np.cos(turn) * prefer[0]
    angle = np.degrees(np.arctan2(aside, ahead))
    angle = np.where(angle > 90, angle - 180, np.where(angle < -90, angle + 180, angle))
    skin = (zone > 0) & (weight > 0.3)
    dark = (ridge >= 0.007) & skin
    if skin.sum() < 50 or (crowded is not None and dark.sum() > crowded * skin.sum()):
        return []
    # Lines of different directions that meet (crow's feet fan out from one point) are told apart by direction.
    bands = 3 if tolerance > 35 else 1
    pieces = []
    for band in range(bands):
        low = -tolerance + band * 2.0 * tolerance / bands
        found = (dark & (angle >= low) & (angle <= low + 2.0 * tolerance / bands)).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(found, connectivity=8)
        pieces += [np.nonzero(labels == index) for index in range(1, count) if stats[index, cv2.CC_STAT_AREA] >= max(6, shortest)]
    lines = []
    for rows, columns in pieces:
        points = np.stack([columns, rows], axis=1).astype(np.float32)
        strength = ridge[rows, columns]
        middle = np.average(points, axis=0, weights=strength)
        values, vectors = np.linalg.eigh(np.cov((points - middle).T) + np.eye(2) * 1e-3)
        axis = vectors[:, 1]
        run = (points[:, 0] - middle[0]) * axis[0] + (points[:, 1] - middle[1]) * axis[1]
        length = float(run.max() - run.min())
        if length < shortest or length < 2.5 * 4 * math.sqrt(float(values[0])):  # long enough, and a line not a blob
            continue
        steps = max(4, int(length / max(2.0, 0.08 * eye)))
        edges = np.linspace(run.min(), run.max() + 1e-3, steps + 1)
        line = []
        for first, last in zip(edges, edges[1:]):
            inside = (run >= first) & (run < last)
            if inside.any():
                line.append(np.average(points[inside], axis=0, weights=strength[inside]))
        if len(line) < 4:
            continue
        line = np.array(line, np.float32)
        stands = stands_out(depth, weight, line, eye)
        if not a_crease(around, line, stands, eye):
            continue
        lines.append(dict(points=line, stands=stands, score=length * float(np.median(stands))))
    lines.sort(key=lambda line: -line["score"])
    return lines[:most]


def measure(bgr, face, like=None):
    """What there is to even out, lift, lighten and lower on this face. `bgr`: floats 0-1, the face in its own
    coordinates. Returns a dict: how much of each item there is in levels 0-255 (`levels`), with its two sides where
    it has two (`sides`), and what the strokes and the check pictures need. With `like` (what an earlier call
    returned, for the picture before it was painted) the same skin, the same patches and the same lines are read
    again, so the two sets of levels compare one thing."""
    shape = bgr.shape[:2]
    eye = face.eye
    placed = like["placed"] if like else zones(face, shape)
    if like:
        weight, wide_weight = like["weight"], like["wide_weight"]
    else:
        colour = skin_colour((bgr * 255).astype(np.uint8), face, placed)
        weight = skin_weight(colour, face, placed, placed["guard"])        # skin to even out: without the nose
        wide_weight = skin_weight(colour, face, placed, placed["features"])  # skin that can shine: with it
    y = lum(bgr).astype(np.float32)
    levels, sides = dict.fromkeys(ITEMS, 0.0), {}

    # Patches: finer than `fine` is texture and stays; wider than `coarse` is the face's form and stays; a patch far
    # from its surroundings is a feature; a line where the light steps is shape. Away from the mask's own edges.
    fine, coarse = max(2.0, face.width / 150.0), max(6.0, face.width / 36.0)
    wide = masked_blur(y, weight, coarse)
    band = masked_blur(y, weight, fine) - wide
    size = np.abs(band)
    keep = np.clip((2.2 * EDGE - size) / (1.2 * EDGE), 0, 1)
    keep = keep * keep * (3 - 2 * keep)
    step = np.hypot(cv2.Sobel(wide, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(wide, cv2.CV_32F, 0, 1, ksize=3)) / 8.0 * coarse
    even = np.clip((STEP[1] - step) / (STEP[1] - STEP[0]), 0, 1)
    keep = keep * even * even * (3 - 2 * even)
    reach = odd(3 * coarse)
    inner = cv2.erode((weight > 0.9).astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
    inner = cv2.GaussianBlur(inner, (0, 0), coarse / 2).astype(np.float32) / 255.0
    territory = inner * np.clip(1.0 - np.maximum(placed["bags"], placed["folds"]), 0, 1)
    patches = -band * keep * territory  # the lightness that evening every patch out would add
    solid = like["solid"] if like else (territory > 0.8) & (size < 2.2 * EDGE)
    for area in EVEN:
        inside = solid & placed["areas"][area]
        if inside.sum() > 200:
            levels[area] = 255.0 * float(size[inside].mean())

    # Under the eyes: how much darker each zone is than the skin right round it, read broadly.
    circles = []
    for one in placed["eyes"]:
        reference = y[(one["ring"] > 0) & (weight > 0.5)]
        core = one["zone"] > 0
        if reference.size < 30 or core.sum() < 30:
            circles.append(None)
            continue
        clear = 1.0 - one["opening"].astype(np.float32) / 255.0
        short = np.clip(float(np.median(reference)) - masked_blur(y, clear, max(1.0, 0.15 * eye)), 0, 0.3)
        circles.append(dict(centre=one["centre"], short=float(short[core].mean()), core=core))
    sides["circles"] = [255.0 * c["short"] if c else 0.0 for c in circles]
    if any(circles):
        levels["circles"] = float(np.mean([255.0 * c["short"] for c in circles if c]))

    # Creases: darker than the skin round them at a crease's own scale, each found as a line.
    clear_weight = np.maximum(weight, 0.05)
    # How much darker each place is than the skin round it, at a crease's own scale. Signed: on a highlight (between
    # the brows, down the nose) a line is still lighter than the face round it, and is a line all the same.
    depth = masked_blur(y, clear_weight, max(4.0, 0.35 * eye)) - masked_blur(y, clear_weight, max(1.0, 0.04 * eye))
    creases = {}
    if like:  # the same lines as before, read again
        for item, found in like["creases"].items():
            creases[item] = [dict(line, stands=stands_out(depth, weight, line["points"], eye)) for line in found]
    else:
        across, down = face.across, face.down
        # The smile lines are followed along where the landmarks say they run: the darkest line near that curve,
        # then the longest stretch of it that stands out by two levels or more.
        creases["folds"] = []
        for index, (curve, outward) in enumerate(placed["curves"]):
            tangent = np.gradient(curve, axis=0)
            tangent /= np.maximum(np.hypot(tangent[:, 0], tangent[:, 1])[:, None], 1e-6)
            normal = np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)
            steps = np.linspace(-0.3 * eye, 0.3 * eye, 25)
            shifts = []
            for point, n in zip(curve, normal):
                where = point + steps[:, None] * n
                shifts.append(steps[int(np.where(at(weight, where) > 0.3, at(depth, where), -1.0).argmax())])
            shifts = np.convolve(np.pad(np.array(shifts), 3, mode="edge"), np.ones(7) / 7, mode="valid")  # one smooth line
            points = (curve + shifts[:, None] * normal).astype(np.float32)
            stands = stands_out(depth, weight, points, eye)
            first, last, start = 0, 0, None
            for position, flag in enumerate(list(stands * 255 >= 2.0) + [False]):
                if flag and start is None:
                    start = position
                elif not flag and start is not None:
                    if position - start > last - first:
                        first, last = start, position
                    start = None
            # (No skin asked for on both sides: a smile line runs along the mouth and the nose, which are kept out.)
            if last - first >= 5 and a_crease(wide_weight, points[first:last], stands[first:last], eye, sides=False):
                creases["folds"].append(dict(points=points[first:last], stands=stands[first:last], side=index))
        searches = (
            ("troughs", [(one["trough"], across, 50, 1, 0.5 * eye, index) for index, one in enumerate(placed["eyes"])], None),
            ("crows", [(one["corner"], across, 55, 3, 0.22 * eye, index) for index, one in enumerate(placed["eyes"])], None),
            ("marionette", [(zone, down, 40, 1, 0.45 * eye, index) for index, (zone, _) in enumerate(placed["corners"])], None),
            ("forehead_lines", [(placed["forehead"], across, 25, 4, 0.9 * eye, None)], 0.3),
            ("frown", [(placed["frown"], down, 30, 3, 0.4 * eye, None)], None),
        )
        for item, zones_, crowded in searches:
            creases[item] = []
            for zone, prefer, tolerance, most, shortest, side in zones_:
                for line in find_creases(y, depth, weight, wide_weight, zone, eye, prefer, tolerance, most, shortest, crowded):
                    creases[item].append(dict(line, side=side))
    if not like:
        # A smile line often runs on past the mouth's corner. Found a second time as the line below the corner, it
        # would be painted twice: what lies along a smile line belongs to the smile line.
        along = [line["points"] for line in creases["folds"]]
        if along:
            along = np.concatenate(along)
            apart = lambda line: np.median(np.hypot(line["points"][:, None, 0] - along[None, :, 0],
                                                    line["points"][:, None, 1] - along[None, :, 1]).min(axis=1))
            creases["marionette"] = [line for line in creases["marionette"] if apart(line) > 0.2 * eye]
    for item in CREASES:
        found = creases.get(item, [])
        if item in ("forehead_lines", "frown"):
            levels[item] = 255.0 * float(np.mean([np.median(line["stands"]) for line in found])) if found else 0.0
        else:  # two sides: one without a line counts as nothing
            each = []
            for side in (0, 1):
                mine = [float(np.median(line["stands"])) for line in found if line["side"] == side]
                each.append(255.0 * float(np.mean(mine)) if mine else 0.0)
            sides[item] = each
            levels[item] = float(np.mean(each))

    # Shine: light off the surface of the skin. Brighter than the skin round it, and with less colour than skin has.
    glow = y - masked_blur(y, wide_weight, max(6.0, face.width / 14.0))
    high, low = bgr.max(axis=-1), bgr.min(axis=-1)
    colourful = (high - low) / np.maximum(high, 1e-3)
    own = float(np.median(colourful[weight > 0.8])) if (weight > 0.8).sum() > 100 else 0.3
    pale = np.clip((own - colourful) / max(1e-3, 0.4 * own), 0, 1)
    wide_inner = cv2.erode((wide_weight > 0.9).astype(np.uint8) * 255,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (odd(coarse), odd(coarse))))
    wide_inner = cv2.GaussianBlur(wide_inner, (0, 0), coarse / 2).astype(np.float32) / 255.0
    shine = np.clip(glow - SHINE_FROM, 0, 0.25) * pale * wide_inner  # only the hot middle of a highlight
    # How far the hot spots stand above the skin round them; nothing when they are under a 300th of the skin.
    shiny = like["shiny"] if like else shine > 0.004
    if shiny.sum() > max(30, 0.003 * float((wide_inner > 0.8).sum())):
        levels["shine"] = 255.0 * max(0.0, float(glow[shiny].mean()))
    return dict(levels=levels, sides=sides, weight=weight, wide_weight=wide_weight, territory=territory, placed=placed,
                patches=patches, circles=circles, creases=creases, shine=shine, shiny=shiny, y=y, coarse=coarse, solid=solid)


def auto_need(item, level):
    if item == "shape":
        return "none"
    low, high = AUTO[item]
    if item == "circles" and level >= NOT_CIRCLES:
        return "none"
    return "none" if level < low else "some"  # never clear: all of it takes someone looking and saying so


def levels_table(measured, needs=None, after=None):
    """The measurements as lines to read: one item a line, with its two sides, what auto makes of it or the need it
    was given, and what it measures after painting."""
    rows = []
    for item in ITEMS:
        level = measured["levels"][item]
        if item == "shape":
            chosen = needs[item] if needs else "auto: none"
            rows.append(f"     {item:15s}    -   {chosen:12s} {WORDS[item]}")
            continue
        chosen = needs[item] if needs else f"auto: {auto_need(item, level)}"
        detail = WORDS[item]
        both = measured["sides"].get(item)
        if both and max(both) > 0:
            detail += f" (picture's left {both[0]:.1f}, right {both[1]:.1f})"
        if item == "circles" and level >= NOT_CIRCLES:
            detail += f"; {NOT_CIRCLES:g} or more is eye makeup or a cast shadow, which auto leaves"
        if item in CREASES and not measured["creases"].get(item):
            detail += "; no line found"
        if not needs and level >= AUTO[item][1] and not (item == "circles" and level >= NOT_CIRCLES):
            detail += "; plain to see: clear is worth considering"
        then = f" -> {after['levels'][item]:4.1f}" if after is not None and needs and needs[item] != "none" else ""
        rows.append(f"     {item:15s} {level:4.1f}{then}   {chosen:12s} {detail}")
    return "\n".join(rows)


# ==========================================
# Strokes: where the brush goes and how strong, for what was measured
# ==========================================

def soft_light_reach(bgr):
    """What white and what black on a Soft Light layer do to the lightness here, 0-1: (up, down)."""
    lifted = np.where(bgr <= 0.25, ((16 * bgr - 12) * bgr + 4) * bgr, np.sqrt(bgr))
    return np.maximum(lum(lifted - bgr), 1e-3), np.maximum(lum(bgr * (1 - bgr)), 1e-3)


def curve_reach(bgr):
    """What the brightening and the darkening curve, in Luminosity, do to the lightness here: (up, down)."""
    out = []
    for points, sign in ((DODGE_CURVE, 1.0), (BURN_CURVE, -1.0)):
        middle = points[1][1] / 255.0  # the curves are one point off the diagonal: a parabola through it is close enough
        curved = bgr + (middle - 0.5) * 4 * bgr * (1 - bgr)
        out.append(np.maximum(sign * (lum(curved) - lum(bgr)), 1e-3))
    return out


def opacity_for(delta, bgr, where, kind):
    """The brush opacity that moves the lightness by `delta` (signed, 0-1) where `where` is true. On gray, white at
    opacity a takes 50% gray a of the way to white, which is a of what the layer can do; on a mask, a is the share of
    the curve that shows."""
    up, down = soft_light_reach(bgr) if kind == "gray" else curve_reach(bgr)
    reach = float((up if delta >= 0 else down)[where].mean()) if where.any() else 0.15
    return abs(delta) / reach


def on_points(shape, points):
    where = np.zeros(shape, bool)
    where[np.clip(points[:, 1].astype(int), 0, shape[0] - 1), np.clip(points[:, 0].astype(int), 0, shape[1] - 1)] = True
    return where


def dabs_for(wanted, bgr, sigmas, most, cap, kind, item):
    """Soft dabs that add up to `wanted` (lightness, signed) as nearly as `most` of them can: at each brush size, the
    places where a dab of that size fits what is still wanted best, the strongest first."""
    up, down = soft_light_reach(bgr) if kind == "gray" else curve_reach(bgr)
    left = wanted.astype(np.float32).copy()
    dabs = []
    for sigma in sigmas:
        size = sigma / DAB_SIGMA
        fitted = 2.0 * blur(left, sigma)  # the height a dab here would best have (the least-squares fit of a Gaussian)
        strength = np.abs(fitted)
        window = odd(3 * sigma)
        peaks = (strength == cv2.dilate(strength, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (window, window)))) \
            & (strength >= 1.2 / 255.0)
        rows, columns = np.nonzero(peaks)
        order = np.argsort(-strength[rows, columns])[:most]
        radius = int(3 * sigma)
        for row, column in zip(rows[order], columns[order]):
            height = float(fitted[row, column])
            reach = float((up if height > 0 else down)[row, column])
            opacity = min(0.35, cap / reach, abs(height) / reach)
            if opacity < 0.02:
                continue
            height = math.copysign(opacity * reach, height)
            dabs.append(dict(item=item, sign=1 if height > 0 else -1, points=[[float(column), float(row)]], size=size,
                             opacity=opacity, lift=height))
            y0, y1 = max(0, row - radius), min(left.shape[0], row + radius + 1)
            x0, x1 = max(0, column - radius), min(left.shape[1], column + radius + 1)
            yy, xx = np.mgrid[y0:y1, x0:x1]
            left[y0:y1, x0:x1] -= height * np.exp(-((yy - row) ** 2 + (xx - column) ** 2) / (2 * sigma * sigma))
    dabs.sort(key=lambda dab: -abs(dab["lift"]))
    return dabs[:most]


def shape_strokes(face, measured, share, bgr, kind):
    """Modelling: a little light down the nose's bridge, on the forehead's middle, the cheeks' tops and the chin; a
    little shade beside the nose and under the cheekbones. Placed by the landmarks, kept to what is skin."""
    apart, eye, across, down = face.apart, face.eye, face.across, face.down
    middle = face.eyes + across * 0.5 * face.turned
    plan = [("nose bridge", [face.eyes + across * 0.35 * face.turned + down * 0.12 * face.length,
                             face.eyes + across * 0.8 * face.turned + down * 0.72 * face.length], 0.2 * apart),
            ("forehead", [face.eyes - down * 1.25 * eye - across * 0.25 * apart, face.eyes - down * 1.25 * eye + across * 0.25 * apart], 0.9 * apart),
            ("chin", [face.mouth + down * 0.5 * face.drop], 0.55 * apart)]
    for centre, side in ((face.eye_a, -1.0), (face.eye_b, 1.0)):
        outward = across * side
        plan.append(("cheek", [centre + down * 1.0 * eye + outward * 0.05 * eye, centre + down * 0.9 * eye + outward * 0.5 * eye], 0.75 * eye))
        plan.append(("nose side", [face.base + outward * 0.2 * apart - down * 0.7 * face.length,
                                   face.base + outward * 0.24 * apart - down * 0.15 * face.length], 0.14 * apart))
        plan.append(("under cheekbone", [middle + outward * 0.95 * apart + down * 0.62 * face.drop,
                                         middle + outward * 0.52 * apart + down * 0.98 * face.drop], 0.34 * apart))
    strokes = []
    for name, points, size in plan:
        points = np.array(points, np.float32)
        if float(at(measured["wide_weight"], points).min()) < 0.4 and name != "nose bridge":
            continue  # not on skin: hair, the picture's edge, a hand
        lift = share * SHAPE_LEVELS[name] / 255.0
        opacity = opacity_for(lift, bgr, on_points(bgr.shape[:2], points), kind)
        if opacity >= 0.01:
            strokes.append(dict(item="shape", sign=1 if lift > 0 else -1, points=points.tolist(), size=size,
                                opacity=min(0.3, opacity), lift=lift))
    return strokes


def plan_strokes(measured, needs, bgr, face, kind):
    """The strokes of one face, in its own coordinates: [dict(item, sign, points, size, opacity, lift)]. sign +1
    lifts (white on gray, white on the brightening curve's mask), -1 lowers."""
    strokes = []
    eye, across, shape = face.eye, face.across, bgr.shape[:2]
    share = {item: FULL.get(item, 1.0) * NEEDS[needs[item]] for item in ITEMS}
    if share["circles"] > 0:
        for circle in measured["circles"]:
            if not circle:
                continue
            lift = min(CAP, share["circles"] * circle["short"])
            opacity = opacity_for(lift, bgr, circle["core"], kind)
            if opacity >= 0.02:
                a, b = circle["centre"] - across * 0.28 * eye, circle["centre"] + across * 0.28 * eye
                strokes.append(dict(item="circles", sign=1, points=[a.tolist(), b.tolist()], size=0.62 * eye,
                                    opacity=min(0.6, opacity), lift=lift))
    for item in CREASES:
        if share[item] <= 0:
            continue
        size = max(5.0, (0.16 if item == "folds" else 0.12) * eye)
        for line in measured["creases"].get(item, []):
            # One stroke along the line, as strong as most of it is dark. (Runs of their own strengths would overlap
            # where they meet, and a brush paints twice where two strokes cross.)
            lift = min(CAP, share[item] * CREASE * math.tanh(float(np.median(line["stands"])) / CREASE))
            opacity = opacity_for(lift, bgr, on_points(shape, line["points"]), kind)
            if lift * 255 >= 1.0 and opacity >= 0.02:
                strokes.append(dict(item=item, sign=1, points=line["points"].tolist(), size=size, opacity=min(0.6, opacity),
                                    lift=lift))
    coarse = measured["coarse"]
    for area in EVEN:
        if share[area] > 0:
            wanted = np.clip(share[area] * measured["patches"], -CAP, CAP) * measured["placed"]["areas"][area]
            strokes += dabs_for(wanted, bgr, (coarse, coarse / 2, max(1.5, coarse / 4)), DABS[area], CAP, kind, area)
    if share["shine"] > 0:
        wanted = -np.clip(share["shine"] * measured["shine"], 0, SHINE_CAP)
        strokes += dabs_for(wanted, bgr, (coarse * 1.5, coarse * 0.75), DABS["shine"], SHINE_CAP, kind, "shine")
    if share["shape"] > 0:
        strokes += shape_strokes(face, measured, share["shape"], bgr, kind)
    return strokes


# ==========================================
# The engine
# ==========================================

class Engine:
    """A PhotoCraft MCP session in a scratch folder, the only folder the engine may touch."""

    def __init__(self):
        self.folder = os.path.realpath(tempfile.mkdtemp(prefix="pcraft-dodge-burn-"))
        self.session = Mcp(find_cli(say, warn), self.folder)

    def path(self, name):
        return os.path.join(self.folder, name)

    def close(self):
        self.session.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def shot(self, name="shot.png"):
        """What the open document looks like now, BGR floats 0-1."""
        path = self.path(name)
        if os.path.exists(path):
            os.remove(path)
        self.session.call("doc_save", {"path": path})
        image = read_raw(path, cv2.IMREAD_COLOR)
        os.remove(path)
        if image is None:
            raise Fail("PhotoCraft did not hand back a picture of the document")
        return image.astype(np.float32) / 255.0


def staged_name(path):
    """The picture's own name when the engine's automation takes it, else a plain one."""
    name = os.path.basename(path)
    stem = name.split(".")[0].rstrip().upper()
    if (":" in name or "\\" in name or name.endswith((".", " ")) or name.startswith(".")
            or stem in {"CON", "PRN", "AUX", "NUL"}):
        return "photo" + os.path.splitext(name)[1]
    return name


def stamp(path):
    status = os.stat(path)
    return {"size": status.st_size, "mtime": round(status.st_mtime, 3)}


def place(source, destination):
    part = destination + ".part"
    shutil.copyfile(source, part)
    os.replace(part, destination)


def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as error:
        raise Fail(f"cannot read the {what} {path}: {error}") from error
    except ValueError as error:
        raise Fail(f"the {what} {path} is not valid JSON: {error}") from error


def find_faces(session, given):
    """The faces to work on, largest first: the ones given by hand, else PhotoCraft's own."""
    if given:
        entries = given.get("faces") if isinstance(given, dict) else given
        if not isinstance(entries, list) or not entries:
            raise Fail("a faces file is {\"faces\": [{\"rightEye\": [x, y], \"leftEye\": [x, y], \"nose\": [x, y], "
                       "\"mouthRight\": [x, y], \"mouthLeft\": [x, y]}]} (right = on the picture's left)")
        faces = [Face(entry.get("landmarks", entry)) for entry in entries]
    else:
        found = session.command("image.detectFaces", {"minScore": 0.6})
        faces = [Face(entry["landmarks"], float(entry.get("score", 1.0))) for entry in (found or {}).get("faces", [])]
    small = [face for face in faces if face.apart < SMALL_FACE]
    if small:
        say(f"[NOTE] {len(small)} face(s) with the eyes under {SMALL_FACE:g} px apart left alone: too few pixels of skin to "
            "measure or to paint on")
    return sorted((face for face in faces if face.apart >= SMALL_FACE), key=lambda face: -face.apart)


def open_source(engine, photo_path):
    """Opens a copy of the photo or document in the engine; returns whether it is layered."""
    if os.path.isdir(photo_path):
        raise Fail(f"{photo_path} is a folder (PhotoCraft's recovery bundle?): open it in PhotoCraft and save it as a file")
    if not os.path.isfile(photo_path):
        raise Fail(f"no file {photo_path}")
    staged = engine.path(staged_name(photo_path))
    shutil.copyfile(photo_path, staged)
    engine.session.call("doc_open", {"path": staged})
    return os.path.splitext(photo_path)[1].lower() in DOCUMENTS


# ==========================================
# Check pictures
# ==========================================

def observed(bgr, middle=None, weight=None):
    """The picture as a retoucher's black & white check layer shows it: this face's skin at middle gray, twice the
    contrast round it. Returns the view (8-bit BGR) and the middle used, so a before and an after share one."""
    gray = lum(bgr) * 255.0
    if middle is None:
        chosen = gray[weight > 0.8] if weight is not None and (weight > 0.8).sum() > 100 else gray
        middle = float(np.median(chosen))
    view = np.clip(128.0 + 2.0 * (gray - middle), 0, 255).astype(np.uint8)
    return cv2.cvtColor(view, cv2.COLOR_GRAY2BGR), middle


def zones_view(bgr, measured, strokes=None):
    """The face with what was placed on it: the skin's areas green, under the eyes cyan, the smile lines' zone
    orange, shine yellow, every crease found a magenta line; and the strokes when there are any (white lifts, black
    lowers; a ring is a dab)."""
    view = (bgr * 255).astype(np.float32)
    tints = [(measured["territory"], (90, 220, 40)), (measured["placed"]["bags"], (255, 200, 0)),
             (measured["placed"]["folds"], (0, 140, 255)), (np.clip(measured["shine"] * 12, 0, 1), (0, 255, 255))]
    for zone, colour in tints:
        tint = np.clip(zone, 0, 1)[..., None] * 0.45
        view = view * (1 - tint) + np.array(colour, np.float32) * tint
    view = np.ascontiguousarray(np.clip(view, 0, 255).astype(np.uint8))
    thick = max(1, int(round(bgr.shape[1] / 500)))
    if strokes is None:
        for found in measured["creases"].values():
            for line in found:
                cv2.polylines(view, [np.round(line["points"]).astype(np.int32).reshape(-1, 1, 2)], False, (255, 0, 255), thick, cv2.LINE_AA)
    for stroke in strokes or ():
        colour = (255, 255, 255) if stroke["sign"] > 0 else (0, 0, 0)
        points = np.round(np.array(stroke["points"])).astype(np.int32)
        if len(points) == 1:
            cv2.circle(view, tuple(int(v) for v in points[0]), max(2, int(stroke["size"] * DAB_SIGMA)), colour, 1, cv2.LINE_AA)
        else:
            cv2.polylines(view, [points.reshape(-1, 1, 2)], False, colour, thick + 1, cv2.LINE_AA)
    return view


def pair(path, left, right, labels, side=1000):
    tiles = [fit(np.ascontiguousarray(tile), side) for tile in (left, right)]
    for tile, text in zip(tiles, labels):
        label(tile, text)
    write_picture(path, side_by_side(tiles))
    return path


def four(path, rows, labels, side=800):
    """Two pairs one above the other: a close-up as it is, and the same in the check view."""
    stacked = []
    for row, texts in zip(rows, labels):
        tiles = [fit(np.ascontiguousarray(tile), side) for tile in row]
        for tile, text in zip(tiles, texts):
            label(tile, text)
        stacked.append(side_by_side(tiles))
    wide = max(image.shape[1] for image in stacked)
    stacked = [np.hstack([image, np.zeros((image.shape[0], wide - image.shape[1], 3), np.uint8)]) for image in stacked]
    write_picture(path, np.vstack([stacked[0], np.full((6, wide, 3), 255, np.uint8), stacked[1]]))
    return path


def close_ups(face, shape):
    """The parts of the face looked at one by one, as slices of its picture: name -> (rows, columns)."""
    eye, apart, across, down = face.eye, face.apart, face.across, face.down

    def around(centre, half_across, up, below):
        corners = [centre + across * sx * half_across + down * sy for sx in (-1, 1) for sy in (-up, below)]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        return (slice(max(0, int(min(ys))), min(shape[0], int(max(ys)) + 1)),
                slice(max(0, int(min(xs))), min(shape[1], int(max(xs)) + 1)))
    return {"eyes": around(face.eyes, 0.5 * apart + 1.45 * eye, 1.0 * eye, 1.2 * eye),
            "mouth": around(face.mouth, 1.0 * apart, 0.75 * face.drop, 0.75 * face.drop),
            "forehead": around(face.eyes - down * 1.3 * eye, 1.0 * apart, 1.0 * eye, 0.85 * eye)}


# ==========================================
# Commands
# ==========================================

def parse_needs(text):
    """--needs: item=need, a group for several items (skin, lines), `all` for every one but the modelling. Later
    words override earlier ones; what is not said is auto."""
    spec = dict.fromkeys(ITEMS, "auto")
    text = (text or "auto").strip().lower()
    if text == "auto":
        return spec
    for part in text.split(","):
        name, _, need = part.partition("=")
        name, need = name.strip().replace("-", "_"), need.strip()
        names = [item for item in ITEMS if item != "shape"] if name == "all" else GROUPS.get(name, (name,))
        if any(item not in spec for item in names) or need not in (*NEEDS, "auto"):
            raise Fail(f"--needs {text!r}: write needs such as skin=some,circles=clear,folds=none. Items: "
                       f"{', '.join(ITEMS)}; groups: skin (forehead, cheeks, chin), lines (forehead_lines, frown, crows, "
                       "marionette), all; needs: clear, some, none, auto")
        for item in names:
            spec[item] = need
    return spec


def targets(args):
    photo_path = os.path.abspath(args.photo)
    stem = os.path.splitext(photo_path)[0]
    out = os.path.abspath(args.out) if getattr(args, "out", None) else stem + SUFFIX + ".jpg"
    if os.path.splitext(out)[1].lower() not in (".jpg", ".jpeg"):
        raise Fail("--out names a .jpg file")
    return photo_path, out, os.path.splitext(out)[0]


def measured_faces(engine, photo_path, faces_file):
    """Opens the photo and measures every face on it: (picture, layered, [(face, box, local face, measured)])."""
    layered = open_source(engine, photo_path)
    picture = engine.shot()
    height, width = picture.shape[:2]
    faces = find_faces(engine.session, load_json(faces_file, "faces file") if faces_file else None)
    rows = []
    for face in faces:
        box = face.box(width, height)
        local = face.shifted(-box[1].start, -box[0].start)
        rows.append((face, box, local, measure(picture[box], local)))
    return picture, layered, rows


def command_look(args):
    need_libraries()
    photo_path, _, out_stem = targets(args)
    engine = Engine()
    try:
        picture, _, rows = measured_faces(engine, photo_path, args.faces)
    finally:
        engine.close()
    if not rows:
        say("[SUCCESS] no face found: nothing measured. If there is a face (turned far away, very small, half hidden), "
            "give its five points with --faces (SKILL.md), or leave this light to the hand")
        return
    checks = out_stem + ".checks"
    os.makedirs(checks, exist_ok=True)
    for number, (face, box, local, measured) in enumerate(rows, 1):
        tag = "" if len(rows) == 1 else f"-{number}"
        crop = picture[box]
        who = "" if len(rows) == 1 else f"face {number} (eyes near x {face.eyes[0]:.0f}, y {face.eyes[1]:.0f}), "
        say(f"[OK] {who}measured, in levels 0-255 (item, how much, what auto makes of it, what it is):\n{levels_table(measured)}")
        seen = observed(crop, weight=measured["weight"])[0]
        view = zones_view(crop, measured)
        say("[READ] " + pair(os.path.join(checks, f"look{tag}.jpg"), seen, view,
                             ("black & white check view: light and dark patches",
                              "green skin, cyan under-eye, orange smile-line zone, yellow shine, magenta = lines found")))
        for name, part in close_ups(local, crop.shape[:2]).items():
            say("[READ] " + pair(os.path.join(checks, f"look{tag}_{name}.jpg"), seen[part], view[part],
                                 (f"{name}: check view", f"{name}: zones and the lines found")))
    say("[SUCCESS] measured only, nothing painted. Read the pictures, settle each need (references/regions.md), then run "
        "apply with --needs")


def command_apply(args):
    need_libraries()
    started = time.time()
    photo_path, out, out_stem = targets(args)
    pcraft, sidecar, checks = out_stem + ".pcraft", out_stem + ".light.json", out_stem + ".checks"
    for path in (out, pcraft):
        if os.path.exists(path) and os.path.samefile(path, photo_path):
            raise Fail(f"{path} is the photo or document itself: the original is never replaced. Give --out another name")
    if not os.path.isdir(os.path.dirname(out)):
        raise Fail(f"no folder {os.path.dirname(out)}")
    spec = parse_needs(args.needs)
    previous = load_json(sidecar, "last run's record") if os.path.exists(sidecar) else {}
    for path, key in ((out, "jpg"), (pcraft, "pcraft")):
        if os.path.exists(path) and not args.force and (previous.get("written") or {}).get(key) != stamp(path):
            raise Fail(f"{path} exists and is not as this script last wrote it (edited in PhotoCraft, or someone "
                       "else's file). Nothing written. Add --force to replace it, or choose another --out")
    engine = Engine()
    try:
        session = engine.session
        picture, layered, rows = measured_faces(engine, photo_path, args.faces)
        if not rows:
            say("[SUCCESS] no face found, so nothing to paint and no file written. If there is a face, give its five "
                "points with --faces (SKILL.md), or leave this light to the hand")
            return
        plans = []
        for number, (face, box, local, measured) in enumerate(rows, 1):
            needs = {item: auto_need(item, measured["levels"][item]) if spec[item] == "auto" else spec[item] for item in ITEMS}
            plans.append((face, box, local, measured, needs, plan_strokes(measured, needs, picture[box], local, args.on)))
        if not any(strokes for *_, strokes in plans):
            for number, (face, box, local, measured, needs, _) in enumerate(plans, 1):
                who = "" if len(plans) == 1 else f"face {number}, "
                say(f"[OK] {who}measured, in levels 0-255 (item, how much, its need, what it is):\n{levels_table(measured, needs)}")
            say("[SUCCESS] nothing to paint: every need is none, or what was measured is too little for a stroke. No "
                "file written or changed. Tell the user what was measured and why this light needs no first pass")
            return
        painted = [item for item in ITEMS if any(any(s["item"] == item for s in strokes) for *_, strokes in plans)]
        first = plans[0][4]
        words = " · ".join(f"{LABELS[item]} {NEED_LABELS[first[item]]}" for item in painted)
        if len(plans) > 1 and any(needs != first for *_, needs, _ in plans):
            words = "每张脸按各自量到的定档"
        if not layered:
            session.command("layer.setProps", {"layer": session.call("doc_inspect")["activeLayer"], "name": "原图"})
        session.command("layer.selectTop")
        layers = {}
        if args.on == "gray":
            made = session.command("layer.new.layer", {"name": f"🩶 中性灰 · {MARK} ({words} · 50% 灰柔光 · 可擦改)",
                                                         "mode": "Soft Light", "fillNeutral": True})
            layers[1] = layers[-1] = made["layer"]
        else:
            for sign, emoji, word, points in ((-1, "🌑", "压暗", BURN_CURVE), (1, "☀️", "提亮", DODGE_CURVE)):
                made = session.command("layer.newAdjustmentLayer.curves", {"points": points})
                layer = made.get("layer") if isinstance(made, dict) else None
                layer = layer if layer is not None else session.call("doc_inspect")["activeLayer"]
                session.command("layer.setProps", {"layer": layer, "blend": "Luminosity",
                                                    "name": f"{emoji} 双曲线 · {word} · {MARK} ({words} · 明度 · 蒙版可擦改)"})
                session.command("layer.layerMask.hideAll", {"layer": layer})
                layers[sign] = layer
        plain = engine.shot()
        if float(np.abs(plain - picture).max()) > 2.5 / 255.0:
            raise Fail("the new layers alone changed the picture: this PhotoCraft does not make them as this skill expects")
        count = 0
        for sign in (-1, 1):
            steps = []
            for face, box, local, measured, needs, strokes in plans:
                for stroke in strokes:
                    if stroke["sign"] != sign:
                        continue
                    points = [[round(x + box[1].start, 1), round(y + box[0].start, 1)] for x, y in stroke["points"]]
                    white = (sign > 0) if args.on == "gray" else True
                    steps.append({"id": "paint.stroke", "params": {
                        "points": points, "size": round(max(1.0, stroke["size"]), 1), "hardness": 0,
                        "opacity": round(stroke["opacity"], 3), "flow": 1.0, "color": "#ffffff" if white else "#000000",
                        "target": "pixels" if args.on == "gray" else "mask"}})
            if not steps:
                continue
            session.command("layer.select", {"layer": layers[sign]})
            for begin in range(0, len(steps), 60):
                session.call("command_batch", {"steps": steps[begin:begin + 60]})
            count += len(steps)
        after = engine.shot()
        os.makedirs(checks, exist_ok=True)
        for name in os.listdir(checks):
            if name.endswith(".jpg"):
                os.remove(os.path.join(checks, name))
        pictures, report = [], []
        for number, (face, box, local, measured, needs, strokes) in enumerate(plans, 1):
            tag = "" if len(plans) == 1 else f"-{number}"
            who = "" if len(plans) == 1 else f"face {number}, "
            before_crop, after_crop = picture[box], after[box]
            landed = lum(after_crop) - lum(before_crop)
            again = measure(after_crop, local, like=measured)
            say(f"[OK] {who}{len(strokes)} strokes. In levels 0-255 (item, measured -> measured again on the same skin and "
                f"lines, its need, what it is):\n{levels_table(measured, needs, again)}")
            row = {"needs": needs, "strokes": len(strokes), "measured": {k: round(v, 2) for k, v in measured["levels"].items()},
                   "after": {k: round(v, 2) for k, v in again["levels"].items()}, "by_item": {}}
            soft = cv2.GaussianBlur(landed, (0, 0), 1.0)
            for item in ITEMS:
                mine = [s for s in strokes if s["item"] == item]
                if not mine:
                    continue
                if item in EVEN or item == "shine":  # dabs overlap and add up: their sum is set against what was wanted
                    if item == "shine":
                        wanted = -np.clip(FULL[item] * NEEDS[needs[item]] * measured["shine"], 0, SHINE_CAP)
                        inside = measured["shiny"]
                    else:
                        wanted = np.clip(FULL[item] * NEEDS[needs[item]] * measured["patches"], -CAP, CAP)
                        inside = (measured["territory"] > 0.5) & measured["placed"]["areas"][item]
                    share = float((wanted * landed)[inside].sum() / max(1e-9, float((wanted * wanted)[inside].sum())))
                    row["by_item"][item] = {"dabs": len(mine), "share": round(share, 2)}
                    say(f"     {item}: {len(mine)} dabs, together {100 * share:.0f}% of the change that was wanted")
                else:  # stroke by stroke: what each was meant to do at its middle, and what the brush did there
                    meant = [255 * abs(s["lift"]) for s in mine]
                    got = [255 * s["sign"] * float(at(soft, np.array([s["points"][len(s["points"]) // 2]], np.float32))[0]) for s in mine]
                    row["by_item"][item] = {"strokes": len(mine), "meant": round(float(np.median(meant)), 1),
                                            "landed": round(float(np.median(got)), 1)}
                    say(f"     {item}: {len(mine)} stroke(s), meant {np.median(meant):.1f} levels at a stroke's middle, "
                        f"landed {np.median(got):.1f}")
            say(f"     over the face: lifts up to {255 * max(0.0, float(landed.max())):.0f} levels, lowers up to "
                f"{255 * max(0.0, float(-landed.min())):.0f}")
            if 255 * float(np.abs(landed).max()) > 1.6 * 255 * CAP:
                warn(f"{who}a place moved by {255 * float(np.abs(landed).max()):.0f} levels, more than the {255 * CAP:.0f} "
                     f"a stroke may: strokes crossed there. Find it in light_map{tag}.jpg and look at it in the close-ups; "
                     "if it shows, lower the need of the items that meet there")
            report.append(row)
            before8, after8 = (before_crop * 255).astype(np.uint8), (after_crop * 255).astype(np.uint8)
            pictures.append(pair(os.path.join(checks, f"light{tag}.jpg"), before8, after8, ("before", f"after ({len(strokes)} strokes)")))
            seen, middle = observed(before_crop, weight=measured["weight"])
            seen_after = observed(after_crop, middle)[0]
            pictures.append(pair(os.path.join(checks, f"light_bw{tag}.jpg"), seen, seen_after, ("check view, before", "check view, after")))
            map_view = cv2.cvtColor(np.clip(128.0 + landed * 255.0 * 4.0, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
            pictures.append(pair(os.path.join(checks, f"light_map{tag}.jpg"), map_view, zones_view(before_crop, measured, strokes),
                                 ("painted, x4: light = lifted, dark = lowered", "zones; strokes: white lift, black lower")))
            for name, part in close_ups(local, before_crop.shape[:2]).items():
                if float(np.abs(landed[part]).max()) * 255 < 1.5:
                    continue  # nothing was painted there
                pictures.append(four(os.path.join(checks, f"light{tag}_{name}.jpg"),
                                     ((before8[part], after8[part]), (seen[part], seen_after[part])),
                                     ((f"{name}: before", "after"), ("check view: before", "after"))))
        outside = np.ones(picture.shape[:2], bool)
        for _, box, *_ in plans:
            outside[box] = False
        if outside.any() and float(np.abs(after - picture)[outside].max()) > 1.5 / 255.0:
            warn("the picture changed outside the faces' own areas: read light.jpg and tell the user")
        staged_jpg, staged_pcraft = engine.path("result.jpg"), engine.path("result.pcraft")
        session.call("doc_save", {"path": staged_jpg, "quality": args.quality})
        session.call("doc_save", {"path": staged_pcraft})
        session.call("doc_close")
        place(staged_jpg, out)
        place(staged_pcraft, pcraft)
        record = {"photo": photo_path, "on": args.on, "needs": args.needs or "auto", "faces": report,
                  "written": {"jpg": stamp(out), "pcraft": stamp(pcraft)}}
        with open(sidecar, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=1)
    finally:
        engine.close()
    say("[OK] check pictures written. Read every one of them before telling anyone it is done:")
    for path in pictures:
        say(f"[READ] {path}")
    say(f"[SUCCESS] {out}, {os.path.basename(pcraft)} ({os.path.getsize(pcraft) / 1e6:.0f} MB: the photo untouched below, "
        f"the {MARK} pass on {KINDS[args.on]} on top); {count} strokes; {time.time() - started:.1f}s")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="A first pass of dodge & burn painted in PhotoCraft, headless")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("look", "measure the face and write look pictures; paints nothing"),
                       ("apply", "paint the pass: PHOTO_光影.jpg and PHOTO_光影.pcraft beside the photo")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("photo", help="a picture (JPEG, PNG, TIFF) or a layered document (.pcraft, .psd)")
        sub.add_argument("--faces", help="a JSON file with each face's five points, when PhotoCraft finds none or the "
                                         "zones in look.jpg sit wrong")
        sub.add_argument("--out", help="the result (.jpg); default PHOTO_光影.jpg beside the photo. Its name also places "
                                       "the check pictures")
        if name == "apply":
            sub.add_argument("--needs", help="how far each item goes, as skin=some,circles=clear,folds=none (clear, some, "
                                             f"none, auto). Items: {', '.join(ITEMS)}; groups: skin, lines, all. What is "
                                             "not said is auto")
            sub.add_argument("--on", choices=sorted(KINDS), default="gray",
                             help="gray (default): a 50%% gray Soft Light layer; curves: the masks of two Curves layers")
            sub.add_argument("--quality", type=int, default=97, help="JPEG quality (default 97)")
            sub.add_argument("--force", action="store_true", help="replace a result that was changed since it was written")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    {"look": command_look, "apply": command_apply}[args.command](args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 out whatever the system's encoding: names here are Chinese
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        main(sys.argv[1:])
    except (Fail, McpError) as error:
        say(f"[Error] {error}")
        sys.exit(1)
