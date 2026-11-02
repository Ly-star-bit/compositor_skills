"""Synthetic-blemish evaluation of the healing the smoothing does first (healed_spots, as --delicious-skin runs it) on
a real portrait (headless).
usage: heal_eval.py make <clean.jpg> <out.jpg> <truth.json> [faint|strong|dense]
       heal_eval.py score <project.comp> <clean.jpg> <spots.jpg> <truth.json>"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import retouch_builder as rb  # noqa: E402


def make(clean_path, out_path, truth_path, strength="faint"):
    # faint: ~5 L* darker / ~5 a* redder; strong: like real acne and dark marks, ~10 L* / ~10 a*; dense: acne-prone
    # skin, 40 spots of mixed strength over the whole face (forehead to jaw), found only by the whole-face search
    dark_amount, red_shift = (0.16, (0.06, -0.035, -0.035)) if strength == "faint" else (0.28, (0.11, -0.06, -0.05))
    wanted = 40 if strength == "dense" else 12
    rng = np.random.default_rng(11)
    clean = np.asarray(Image.open(clean_path).convert("RGB"))
    faces = rb.detect_faces(clean)
    x, y, w, h = faces[0]
    skin, _, hair = rb.skin_mask(clean, faces)
    guard = rb.feature_mask(skin.shape, faces)
    relative = rb.texture(clean, skin, faces)
    img = clean.astype(np.float32) / 255
    spots = []
    regions = [((0.15, 0.32), (0.48, 0.70)), ((0.68, 0.85), (0.48, 0.70)), ((0.32, 0.68), (0.20, 0.28)),
               ((0.38, 0.62), (0.93, 1.02))]
    if strength == "dense":  # out to the temples, the top of the forehead, the outer cheeks and the jaw
        regions = [((0.02, 0.30), (0.40, 0.85)), ((0.70, 0.98), (0.40, 0.85)), ((0.15, 0.85), (-0.08, 0.22)),
                   ((0.20, 0.80), (0.88, 1.08))]
    tries = 0
    while len(spots) < wanted and tries < 40000:
        tries += 1
        (ax, bx), (ay, by) = regions[len(spots) % len(regions)]
        cx, cy = x + rng.uniform(ax, bx) * w, y + rng.uniform(ay, by) * h
        r = rng.uniform(w / 160, w / 60)
        ix, iy, R = int(cx), int(cy), int(2.5 * r)
        window = (slice(iy - R, iy + R), slice(ix - R, ix + R))
        if skin[window].min() < 230 or guard[window].any() or relative[window].max() > 1.6:
            continue
        if any(np.hypot(cx - s[0], cy - s[1]) < (4 if strength == "dense" else 5) * max(r, s[2]) for s in spots):
            continue
        spots.append((float(cx), float(cy), float(r), "dark" if len(spots) % 2 == 0 else "red"))
    height, width = img.shape[:2]
    for (cx, cy) in ((width * 0.1, height * 0.08), (width * 0.85, height * 0.12), (width * 0.5, height * 0.85)):
        spots.append((cx, cy, w / 70, "dark-bg"))
    yy, xx = np.mgrid[0:height, 0:width]
    for index, (cx, cy, r, kind) in enumerate(spots):
        x0, x1, y0, y1 = int(cx - 4 * r), int(cx + 4 * r), int(cy - 4 * r), int(cy + 4 * r)
        g = np.exp(-((xx[y0:y1, x0:x1] - cx) ** 2 + (yy[y0:y1, x0:x1] - cy) ** 2) / (2 * (r / 1.3) ** 2))[..., None]
        if strength == "dense" and not kind.endswith("bg") and index % 3:  # most acne marks: a little darker and redder
            img[y0:y1, x0:x1] *= (1 - 0.10 * g)
            img[y0:y1, x0:x1] += g * np.array((0.035, -0.02, -0.02), np.float32)
        elif kind.startswith("dark"):
            img[y0:y1, x0:x1] *= (1 - dark_amount * g)
        else:
            img[y0:y1, x0:x1] += g * np.array(red_shift, np.float32)
    Image.fromarray((np.clip(img, 0, 1) * 255).round().astype(np.uint8)).save(out_path, quality=95)
    json.dump({"face": [x, y, w, h], "spots": spots}, open(truth_path, "w"))
    print(f"{len(spots)} spots ({sum(1 for s in spots if not s[3].endswith('bg'))} on skin), "
          f"radius {min(s[2] for s in spots):.1f}-{max(s[2] for s in spots):.1f} px, face width {w:.0f}")


def contrast(image, cx, cy, r):
    """How much darker (L*) and redder (a*) the spot's center is than the skin ring around it."""
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2Lab)
    yy, xx = np.ogrid[-int(cy):image.shape[0] - int(cy), -int(cx):image.shape[1] - int(cx)]
    d2 = xx * xx + yy * yy
    center = d2 <= (0.6 * r) ** 2
    ring = (d2 >= (2.2 * r) ** 2) & (d2 <= (3.2 * r) ** 2)
    return (lab[..., 0][ring].mean() - lab[..., 0][center].mean(),
            lab[..., 1][center].mean() - lab[..., 1][ring].mean())


def score(comp, clean_path, spots_path, truth_path):
    """The spots healed_spots finds and heals on the project's photo (in this process, as the smoothing module does
    before smoothing): recall of the planted ones, and what the healing did to them."""
    truth = json.load(open(truth_path))
    project = rb.Project.open(comp)
    work = project.base_rgb().astype(np.float32) / 255
    clean = np.asarray(Image.open(clean_path).convert("RGB")).astype(np.float32) / 255
    blem = np.asarray(Image.open(spots_path).convert("RGB")).astype(np.float32) / 255
    found = rb.healed_spots(project, work, 1.0)
    if found is None:
        print("no spots found")
        return
    after, mask, _, acne = found
    print(f"skin taken for acne-prone: {'yes' if acne else 'no'}")
    hit, missed, bg = 0, [], 0
    rows = []
    hp = lambda a: (lambda g: g - cv2.GaussianBlur(g, (0, 0), 2.0))(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY))
    hp_after = hp(after)
    for (cx, cy, r, kind) in truth["spots"]:
        healed = mask[int(cy), int(cx)] > 128
        if kind.endswith("bg"):
            bg += healed
            continue
        if not healed:
            missed.append((round(cx), round(cy), round(r, 1), kind))
            continue
        hit += 1
        yy, xx = np.ogrid[-int(cy):mask.shape[0] - int(cy), -int(cx):mask.shape[1] - int(cx)]
        d2 = xx * xx + yy * yy
        rows.append((contrast(clean, cx, cy, r), contrast(blem, cx, cy, r), contrast(after, cx, cy, r),
                     hp_after[d2 <= r * r].std() * 255,
                     hp_after[(d2 >= (2.2 * r) ** 2) & (d2 <= (3.2 * r) ** 2)].std() * 255))
    n, labels = cv2.connectedComponents((mask > 128).astype(np.uint8))
    inside = {labels[int(s[1]), int(s[0])] for s in truth["spots"] if not s[3].endswith("bg")}
    extra = (n - 1) - len(inside - {0})
    on_skin = sum(1 for s in truth["spots"] if not s[3].endswith("bg"))
    print(f"recall {hit}/{on_skin}; background spots healed {bg}/3; healed areas without a planted spot: {extra}")
    if missed:
        print("missed:", missed)
    if rows:
        arr = np.array([[c[0][0], c[1][0], c[2][0], c[0][1], c[1][1], c[2][1], c[3], c[4]] for c in rows])
        m = arr.mean(axis=0)
        print(f"darkness vs ring (L*): clean {m[0]:.1f} -> with spot {m[1]:.1f} -> healed {m[2]:.1f}")
        print(f"redness vs ring (a*):  clean {m[3]:.1f} -> with spot {m[4]:.1f} -> healed {m[5]:.1f}")
        print(f"fine texture std: healed spots {m[6]:.2f} vs surrounding skin {m[7]:.2f}")
    outside = mask == 0
    print(f"pixels changed outside the heal mask: {int((np.abs(after[outside] - work[outside]) * 255 > 1.01).sum())}")


if __name__ == "__main__":
    {"make": make, "score": score}[sys.argv[1]](*sys.argv[2:])
