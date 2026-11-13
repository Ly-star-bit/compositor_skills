"""Measured checks of the automatic retouching on real portraits (headless). Each prints its numbers and BAD for any
that falls below the floor recorded when it was tuned, so a change that makes things worse shows up.

usage: bench.py skin | smoothing | light | lightcheck <photo> | cg <folder with built projects> <CGComposite binary>
       | adjust <AdjustReference binary>
Portraits come from $TESTDATA (tests/run.sh fetches them): public-domain NASA portraits of several skin tones.
"""
import json
import os
import subprocess
import sys
import time

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import retouch_builder as rb  # noqa: E402

DATA = os.environ.get("TESTDATA", os.path.expanduser("~/Library/Caches/compositor-retouch/tests/data"))
PORTRAITS = ["collins_orig.jpg", "S92-40463.jpg", "jsc2020e032979.jpg", "jsc2014e037863.jpg", "jsc2007e036047.jpg",
             "jsc2023e047050_alt.jpg"]
failures = []


def lab(rgb):
    return cv2.cvtColor(rgb.astype(np.float32), cv2.COLOR_RGB2Lab)


def rgb_of(lab_image):
    return np.clip(cv2.cvtColor(lab_image.astype(np.float32), cv2.COLOR_Lab2RGB), 0, 1)


def check(label, value, floor=None, ceiling=None):
    bad = (floor is not None and value < floor) or (ceiling is not None and value > ceiling)
    if bad:
        failures.append(label)
    return f"{label} {value:.2f}{' BAD' if bad else ''}"


_portraits = {}


def portrait(name):
    """The photo (0-1), faces, skin mask and the rest, as the skill computes them."""
    if name not in _portraits:
        rgb8 = np.asarray(Image.open(os.path.join(DATA, name)).convert("RGB"))
        faces, small = rb.detect(rgb8, True)
        people = rb.person_matte(small, rgb8) if small is not None else None
        interpreter = rb.parsing_model()
        parts = rb.parse_people(rgb8, faces, interpreter) if interpreter is not None else None
        skin, _, hair = rb.skin_mask(rgb8, faces, people, parts)
        _portraits[name] = dict(name=name, rgb8=rgb8, rgb=rgb8 / 255.0, faces=faces, people=people, parts=parts,
                                skin=skin, hair=hair, face_width=max(face[2] for face in faces))
    return _portraits[name]


def blob(shape, skin, center, radii, soft):
    mask = np.zeros(shape, np.float32)
    cv2.ellipse(mask, (int(center[0]), int(center[1])), (max(1, int(radii[0])), max(1, int(radii[1]))), 0, 0, 360, 1, -1)
    return np.clip(cv2.GaussianBlur(mask, (0, 0), soft) * (skin / 255.0), 0, 1)


# ---- skin mask: coverage of the lit lower face, nothing outside the people ----

def lower_face(shape, faces):
    """Inside the jaw line, below the eyes, without eyes and lips: skin, but for beards."""
    mask = rb.lower_face(shape, rb.landmark_faces(faces)) if rb.landmark_faces(faces) else np.zeros(shape, bool)
    return mask & (rb.feature_mask(shape, faces, ("eyes", "mouth")) == 0)


def bench_skin():
    for name in PORTRAITS + ["astronaut.png"]:
        case = portrait(name)
        tone = cv2.inRange(cv2.cvtColor(case["rgb8"], cv2.COLOR_RGB2YCrCb), (0, 133, 77), (255, 173, 127))
        _, brightness = rb.skin_color(case["rgb8"], case["faces"], tone, case["people"])
        lit = lower_face(case["skin"].shape, case["faces"]) & (brightness >= 0.12)
        coverage = (case["skin"] > 128)[lit].mean() * 100
        outside = ((case["skin"] > 128) & (case["people"] < 0.5)).sum() / max(1, (case["skin"] > 128).sum()) * 100 \
            if case["people"] is not None else 0.0
        print(f"  {name[:22]:22s} {check('lit lower-face coverage %', coverage, floor=95)}  "
              f"{check('outside the person %', outside, ceiling=1)}  model {'yes' if case['parts'] else 'no'}")


# ---- color evening: painted faults taken out, a painted cheek flush kept, clean skin left alone ----


def landmarked(case):
    """The largest face, which the painted tests place their faults by: None when it has no landmarks."""
    face = max(case["faces"], key=lambda f: f[2])
    if not getattr(face, "marks", None):
        print(f"  {case['name']}: no landmarks on the largest face, skipped")
        return None
    return face


# ---- creases: painted in, measured on the way out ----


# ---- skin smoothing: painted dark spots faded, the face's form (smile lines, under-eye, feature shadows) kept ----

def fold_zone(shape, faces):
    """The smile lines (from beside the nostrils to past the mouth corners, bowing outwards) and the bags under the
    eyes, from the landmarks, feathered (8-bit): the real creases the smoothing must keep the shape of. (The skill's
    former folds module drew its mask this way; the bench keeps the zone so the metric stays comparable.)"""
    lines, bags = np.zeros(shape, np.uint8), np.zeros(shape, np.uint8)
    widths = []
    for face in rb.landmark_faces(faces):
        marks = face.marks
        if "nose" not in marks or "outerLips" not in marks:
            continue
        eye = rb.eye_width(marks)
        across, down, tilt = rb._face_axes(marks)
        nose, lips = marks["nose"], marks["outerLips"]
        for side in (-1.0, 1.0):
            outward = across * side
            wing = nose[np.argmax(nose @ outward)]
            corner = lips[np.argmax(lips @ outward)]
            start = wing + outward * 0.2 * eye + down * 0.05 * eye
            end = corner + outward * 0.3 * eye + down * 0.35 * eye
            bow = (start + end) / 2 + outward * 0.2 * eye
            curve = [(1 - t) ** 2 * start + 2 * (1 - t) * t * bow + t ** 2 * end for t in np.linspace(0, 1, 24)]
            rb._line(lines, np.array(curve), 0.45 * eye)
        clear = rb.odd(max(3, eye / 6))
        for side in ("left", "right"):
            eye_points = marks[f"{side}Eye"]
            middle = eye_points.mean(axis=0)
            lower_lid = float(((eye_points - middle) @ down).max())
            crescent = np.zeros(shape, np.uint8)
            rb._ellipse_at(crescent, middle + down * (lower_lid + 0.22 * eye), (0.55 * eye, 0.28 * eye), tilt)
            opening = np.zeros(shape, np.uint8)
            rb._fill(opening, eye_points)
            opening = cv2.dilate(opening, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (clear, clear)))
            bags |= crescent & ~opening
        widths.append(eye)
    width = np.median(widths) if widths else 1
    return np.maximum(rb._feathered(lines, width * 0.15), rb._feathered(bags, width * 0.12, 1.0))


def smoothing_case(name):
    """The photo with acne-sized dark spots painted on cheeks, chin and forehead, and the zones the bench reads:
    clean skin (texture), skin next to strong edges (halos, color fringes) and the real smile lines and under-eye
    bags from the landmarks (form)."""
    case = portrait(name)
    face = landmarked(case)
    if face is None:
        return None
    (x, y, w, h), shape, skin = face, case["skin"].shape, case["skin"]
    spots = np.zeros(shape, np.float32)
    for fx, fy in ((.24, .62), (.76, .60), (.30, .76), (.70, .78), (.42, .14), (.62, .16)):
        disc = np.zeros(shape, np.float32)
        cv2.circle(disc, (int(x + w * fx), int(y + h * fy)), max(1, int(w / 70)), 1, -1)
        spots = np.maximum(spots, cv2.GaussianBlur(disc, (0, 0), max(0.8, w / 200)))
    spots *= skin / 255.0
    painted = lab(case["rgb"])
    painted[..., 0] -= 9 * spots
    painted[..., 1] += 4 * spots
    painted[..., 2] += 3 * spots
    lightness = lab(case["rgb"])[..., 0]
    blurred = cv2.GaussianBlur(lightness, (0, 0), 1.0)
    gradient = cv2.magnitude(cv2.Sobel(blurred, cv2.CV_32F, 1, 0), cv2.Sobel(blurred, cv2.CV_32F, 0, 1))
    box = (slice(max(0, int(y - .2 * h)), int(y + 1.3 * h)), slice(max(0, int(x - .2 * w)), int(x + 1.2 * w)))
    edges = np.zeros(shape, np.uint8)
    edges[box] = gradient[box] > np.percentile(gradient[box], 97)
    reach = 2 * max(2, int(case["face_width"] / 40)) + 1
    near = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach))) > 0
    away = cv2.dilate((spots > 0.01).astype(np.uint8), np.ones((9, 9), np.uint8)) == 0
    solid = skin > 200
    guard = np.maximum(rb.feature_mask(shape, case["faces"]), 255 - rb.inner_face(shape, case["faces"]))
    return dict(case, spots=spots, painted=rgb_of(painted), near=solid & near & away,
                clean=solid & (guard == 0) & ~near & away, folds=solid & (fold_zone(shape, case["faces"]) > 0) & away)


def smoothing_row(case, engine, texture="natural"):
    def smoothed(rgb):
        rgb8 = rb.to8(rgb)
        if engine == "form":
            layer = rb.smooth_skin_form(rgb8, case["skin"], case["faces"], case["face_width"],
                                        max(1.5, case["face_width"] / rb.TEXTURES[texture][0]))
        else:
            layer = rb.smooth_skin(rgb8, engine, max(1.5, case["face_width"] / 150), max(4.0, case["face_width"] / 30))
        return lab(rb.blend(rgb.astype(np.float32), layer.astype(np.float32) / 255.0,
                            case["skin"].astype(np.float32) / 255.0, "Normal"))
    photo, after, painted_after = lab(case["rgb"]), smoothed(case["rgb"]), smoothed(case["painted"])
    fw = case["face_width"]
    mid = lambda lightness: cv2.GaussianBlur(lightness, (0, 0), max(1.0, fw / 150)) - cv2.GaussianBlur(lightness, (0, 0), fw / 25)
    pores = lambda lightness: lightness - cv2.GaussianBlur(lightness, (0, 0), 1.5)
    core = case["spots"] > 0.5
    painted = lab(case["painted"])
    return {"spots gone %": 100 * (1 - (painted_after[..., 0] - after[..., 0])[core].mean()
                                   / (painted[..., 0] - photo[..., 0])[core].mean()),
            "folds kept %": 100 * mid(after[..., 0])[case["folds"]].std() / mid(photo[..., 0])[case["folds"]].std(),
            "pores kept %": 100 * pores(after[..., 0])[case["clean"]].std() / pores(photo[..., 0])[case["clean"]].std(),
            "edge dL": float(np.abs(after[..., 0] - photo[..., 0])[case["near"]].mean()),
            "edge dC": float(np.hypot(*(after[..., 1:] - photo[..., 1:])[case["near"]].T).mean())}


def bench_smoothing():
    rows = {"form": [], "guided": [], "soft": [], "smooth": []}
    for name in PORTRAITS:
        case = smoothing_case(name)
        if case is None:
            continue
        for engine in rows:
            row = smoothing_row(case, "form", engine) if engine in ("soft", "smooth") else smoothing_row(case, engine)
            rows[engine].append(row)
            print(f"  {name[:22]:22s} {engine:6s} " + "  ".join(f"{k} {v:.1f}" for k, v in row.items()))
    mean = {engine: {key: float(np.mean([row[key] for row in found])) for key in found[0]} for engine, found in rows.items()}
    floors = {"spots gone %": 55, "folds kept %": 72, "pores kept %": 90}
    ceilings = {"edge dL": 3.1, "edge dC": 2.7}
    print("  MEAN form    " + "  ".join(check(k, v, floors.get(k), ceilings.get(k)) for k, v in mean["form"].items()))
    print("  MEAN guided  " + "  ".join(f"{k} {v:.2f}" for k, v in mean["guided"].items()))
    form, guided = mean["form"], mean["guided"]
    print("  form vs guided: " + "  ".join([
        check("folds kept, points more", form["folds kept %"] - guided["folds kept %"], floor=4),
        check("edge dL ratio", form["edge dL"] / guided["edge dL"], ceiling=0.9),
        check("edge dC ratio", form["edge dC"] / guided["edge dC"], ceiling=0.9),
        check("spots gone, points more", form["spots gone %"] - guided["spots gone %"], floor=0)]))
    # --texture: softer settings take out more and keep less of the pore texture, within the measured ranges
    for texture, pores in (("soft", 80), ("smooth", 70)):
        print(f"  MEAN {texture:7s} " + "  ".join(check(k, v, {"pores kept %": pores}.get(k), ceilings.get(k))
                                               for k, v in mean[texture].items()))
    print("  texture order: " + "  ".join([
        check("soft takes out more spots than natural", mean["soft"]["spots gone %"] - form["spots gone %"], floor=2),
        check("smooth more than soft", mean["smooth"]["spots gone %"] - mean["soft"]["spots gone %"], floor=1)]))


# ---- automatic dodge & burn: the dual curves' painted masks deliver the correction, and even out the skin ----


# ---- the skill's compositing of glow layers vs Core Graphics drawing them the way Compositor's exporter does ----

def bench_light():
    """The AI light pass at full needs on each portrait: what it measures, how much of each it takes away, and what
    it must leave alone (texture, the face's form, everything outside its zones)."""
    print("  portrait               measured: skin circles folds | patches -%  under-eye -%  creases -% | texture x  form "
          "moved  off (gray, curves)")
    for name in PORTRAITS:
        case = portrait(name)
        under = case["rgb"].astype(np.float32)
        width = case["face_width"]
        box, maps, levels, zones = rb.light_measure(under, case["skin"], case["faces"], width)
        part = sum(rb.LIGHT_FULL[item] * maps[item] for item in rb.LIGHT_ITEMS)
        part = np.clip(part, -rb.LIGHT_CAP, rb.LIGHT_CAP).astype(np.float32)
        part[np.abs(part) < 0.5 / 255.0] = 0.0
        crop = under[box]
        gray = np.floor(128.0 + (rb.light_gray(crop, part) - 0.5) * 255.0 + 0.5)
        gray = np.where(np.abs(part) > 0, gray, 128.0) / 255.0
        after = rb.blend(crop, np.dstack([gray] * 3).astype(np.float32), np.ones(part.shape, np.float32), "Soft Light")
        burn, dodge = rb.light_masks(crop, part)
        curved = crop
        for points, mask in ((rb.BURN_CURVE, burn), (rb.DODGE_CURVE, dodge)):
            curved = rb.blend(curved, rb.adjusted(curved, rb.curves(points)), mask.astype(np.float32), "Luminosity")
        touched = np.abs(part) > 0
        off_gray = 255.0 * float(np.abs(rb._lum(after) - rb._lum(crop) - part)[touched].mean())
        off_curves = 255.0 * float(np.abs(rb._lum(curved) - rb._lum(crop) - part)[touched].mean())
        full = under.copy()
        full[box] = after
        _, _, left, _ = rb.light_measure(full, case["skin"], case["faces"], width)
        drop = {item: 100.0 * (1 - left[item] / levels[item]) if levels[item] > 0.5 else float("nan") for item in rb.LIGHT_ITEMS}
        territory = zones[0] > 0.8
        y0, y1 = rb._lum(crop), rb._lum(after)
        fine = max(1.0, width / 300.0)
        texture = float(np.abs(y1 - cv2.GaussianBlur(y1, (0, 0), fine))[territory].mean()
                        / max(1e-6, np.abs(y0 - cv2.GaussianBlur(y0, (0, 0), fine))[territory].mean())) if territory.any() else 1.0
        form = 255.0 * float(np.abs(rb.gaussian(y1, width / 6.0) - rb.gaussian(y0, width / 6.0)).max())
        row = [f"{levels['skin']:5.1f} {levels['circles']:6.1f} {levels['folds']:6.1f} |"]
        row.append(check("patches", drop["skin"], floor=15, ceiling=45) if levels["skin"] >= 2 else "patches   -")
        row.append(check("under-eye", drop["circles"], floor=15, ceiling=70) if levels["circles"] >= 4 else "under-eye   -")
        row.append(check("creases", drop["folds"], floor=10, ceiling=70) if levels["folds"] >= 5 else "creases   -")
        row.append("| " + check("texture", texture, floor=0.96, ceiling=1.03))
        row.append(check("form", form, ceiling=2.5))
        row.append(check("gray", off_gray, ceiling=0.5))
        row.append(check("curves", off_curves, ceiling=0.5))
        print(f"  {name[:22]:22s} " + "  ".join(row))
        outside = np.ones(under.shape[:2], bool)
        outside[box] = ~touched
        if np.abs(full[outside] - under[outside]).max() > 0.6 / 255.0:  # (8-bit gray has no exact middle: 128 moves 0.2)
            failures.append(f"{name}: changed outside what it painted")
            print("  BAD: pixels changed where nothing was painted")


def light_projects(photo):
    """What tests/run.sh's light suite built in the working folder: lf.comp (the pass on gray, at full needs) with
    its render, l.comp (made again with other needs; before.json is its manifest from before), k.comp (on the two
    curves) with its render, d.comp (with the daily preset)."""
    def say(condition, label, detail=""):
        print(("  ok: " if condition else "  BAD: ") + label + ("" if condition else f" ({detail})"))
        if not condition:
            failures.append(label)

    def layers(comp):
        return json.load(open(os.path.join(comp, "manifest.json")))["layers"]

    def image(comp, name):
        return np.asarray(Image.open(os.path.join(comp, "images", name)))

    original = np.asarray(Image.open(photo).convert("RGB")).astype(int)
    full = layers("lf.comp")
    passes = [layer for layer in full if rb.LIGHT_MARK in layer["name"]]
    folder = next((layer for layer in full if layer.get("isGroup") and layer["name"].startswith("🌓")), {})
    say(len(passes) == 1 and passes[0]["name"].startswith("🩶") and passes[0]["blendMode"] == "Soft Light"
        and passes[0].get("parentID") == folder.get("id") and "肤色不匀 全量 · 黑眼圈 全量 · 法令纹 全量" in passes[0]["name"],
        "on gray: one Soft Light layer in the light folder, named by its needs", [layer["name"] for layer in passes])
    gray = image("lf.comp", passes[0]["imageFile"])[..., :3].astype(int)
    plain = (gray == 128).all(axis=-1)
    say((gray[..., 0] == gray[..., 1]).all() and (gray[..., 1] == gray[..., 2]).all() and 0.9 < plain.mean() < 0.999
        and 25 <= gray.min() and gray.max() <= 230,
        f"its pixels are neutral gray, plain 50% outside what it painted ({100 * plain.mean():.1f}% of the frame)",
        f"min {gray.min()}, max {gray.max()}")
    render = np.asarray(Image.open("l.render.png").convert("RGB")).astype(int)
    moved = np.abs(render - original).max(axis=-1)
    lum = lambda rgb: rgb @ np.array([0.3, 0.59, 0.11])
    lifted, lowered = gray[..., 0] > 133, gray[..., 0] < 123
    say(moved[plain].max() <= 1 and (lum(render) - lum(original))[lifted].mean() > 0.5
        and (lum(render) - lum(original))[lowered].mean() < -0.5,
        "the render is the photo where the layer is plain, lighter where it is lighter and darker where it is darker",
        f"outside {moved[plain].max()}, lifted {(lum(render) - lum(original))[lifted].mean():.2f}, "
        f"lowered {(lum(render) - lum(original))[lowered].mean():.2f}")
    redone = [layer for layer in layers("l.comp") if rb.LIGHT_MARK in layer["name"]]
    before = [layer for layer in json.load(open("before.json"))["layers"] if rb.LIGHT_MARK in layer["name"]]
    say(len(redone) == 1 and redone[0]["id"] == before[0]["id"] and "肤色不匀 一半 · 黑眼圈 一半 · 法令纹 不修" in redone[0]["name"]
        and len(layers("l.comp")) == len(json.load(open("before.json"))["layers"]),
        "made again: the same layer in the same place, renamed by the new needs", [layer["name"] for layer in redone])
    milder = image("l.comp", redone[0]["imageFile"])[..., 0].astype(int)
    say(np.abs(milder - 128).sum() < 0.75 * np.abs(gray[..., 0] - 128).sum(), "and painted less at lower needs")
    pair = [layer for layer in layers("k.comp") if rb.LIGHT_MARK in layer["name"]]
    say(len(pair) == 2 and [layer["name"][0] for layer in pair] == ["🌑", "☀"] and all(
        layer["blendMode"] == "Luminosity" and (layer.get("adjustment") or {}).get("kind") == "Curves" and layer.get("maskFile")
        for layer in pair), "on curves: a darkening and a brightening Curves layer in Luminosity, each with its mask",
        [layer["name"] for layer in pair])
    burn, dodge = (image("k.comp", layer["maskFile"]).astype(int) for layer in pair)
    burn, dodge = (mask if mask.ndim == 2 else mask[..., 0] for mask in (burn, dodge))
    say(dodge.max() > 30 and burn.max() > 30 and ((burn > 8) & (dodge > 8)).mean() < 0.001
        and (dodge[lifted] > 0).mean() > 0.9 and (burn[lowered] > 0).mean() > 0.9,
        "their masks are painted where the gray layer lifts and lowers, never both in one place",
        f"dodge max {dodge.max()}, burn max {burn.max()}")
    curved = np.asarray(Image.open("k.render.png").convert("RGB")).astype(int)
    apart = np.abs(lum(curved) - lum(render))
    # (Not to the level everywhere: gray in Soft Light can do little to a highlight, where the curve still can.)
    say(apart[~plain].mean() < 0.6 and np.percentile(apart[~plain], 99.5) <= 3,
        "and the two kinds land on the same lightness",
        f"mean {apart[~plain].mean():.2f}, 99.5% within {np.percentile(apart[~plain], 99.5):.1f}, max {apart.max():.0f} levels apart")
    daily = layers("d.comp")
    grays = [layer for layer in daily if layer["name"].startswith("🩶")]
    say(len(grays) == 1 and rb.LIGHT_MARK in grays[0]["name"] and any(layer["name"].startswith(rb.SMOOTH) for layer in daily)
        and any(layer["name"].startswith(rb.OBSERVATION_PREFIX) for layer in daily),
        "with the daily preset: the pass is the preset's gray layer, beside the smoothing and the observation folders",
        [layer["name"] for layer in daily])


def bench_cg(folder, binary):
    for comp in sorted(p for p in os.listdir(folder) if p.endswith(".comp")):
        path = os.path.join(folder, comp)
        project = rb.Project.open(path)
        by_id = {r["id"]: r for r in project.layers}
        layers = []
        for record, _ in rb.render_order(project.layers):
            if record.get("isGroup") or not record.get("imageFile") or record.get("blendMode") != "Screen":
                continue
            mask = record.get("maskFile") or by_id.get(record.get("parentID"), {}).get("maskFile")
            overlay = (record.get("effects") or {}).get("colorOverlay")
            layers.append({"image": os.path.join(path, "images", record["imageFile"]),
                           "mask": os.path.join(path, "images", mask) if mask else None,
                           "overlay": [overlay[k] for k in ("red", "green", "blue", "opacity")]
                           if overlay and overlay.get("enabled", True) else None,
                           "opacity": record["opacity"]})
        spec = {"base": os.path.join(path, "images", project.base_record()["imageFile"]), "out": path + ".cg.png",
                "layers": layers}
        with open(path + ".spec.json", "w") as f:
            json.dump(spec, f)
        subprocess.run([binary, path + ".spec.json"], check=True)
        drawn = np.asarray(Image.open(spec["out"]).convert("RGB")).astype(np.int16)
        ours = rb.to8(project.composite_top()).astype(np.int16)
        difference = np.abs(drawn - ours)
        print(f"  {comp:24s} glow layers {len(layers)}  {check('mean diff (levels)', difference.mean(), ceiling=0.5)}  "
              f"{check('max diff (levels)', float(difference.max()), ceiling=3)}")


# ---- adjustment layers: the skill's ports against Compositor's own code (AdjustReference), pixel for pixel ----

def adjustment_cases():
    yield "curves, all channels", rb._channel_curves([(0, 0), (64, 40), (128, 150), (255, 255)], red=[(0, 10), (255, 240)],
                                                     green=[(0, 0), (100, 90), (200, 215), (255, 255)],
                                                     blue=[(0, 30), (120, 110), (255, 230)])
    levels = rb.adjustment("Levels")
    levels["levels"]["ranges"][0].update(black=10, white=240, gamma=1.2)
    levels["levels"]["ranges"][2].update(gamma=0.8, outputBlack=5, outputWhite=250)
    yield "levels", levels
    yield "exposure", rb.adjustment("Exposure", exposureSettings={"exposure": 0.7, "offset": -0.02, "gamma": 1.1})
    yield "hue/saturation by range", rb.hue_saturation("Reds", {"Reds": (10, -40, 5), "Master": (0, -10, 8), "Blues": (-20, 30, -10)})
    inverted = rb.hue_saturation("Cyans", {"Cyans": (0, -80, 0)})
    inverted["hsvSettings"]["invertRange"] = True
    yield "hue/saturation, inverted range", inverted
    colorize = rb.hue_saturation("Master", {"Master": (30, 40, -10)})
    colorize["hsvSettings"]["colorize"] = True
    yield "hue/saturation, colorize", colorize
    yield "hue/saturation, saturation +100", rb.hue_saturation("Master", {"Master": (-170, 100, -40)})
    yield "color balance", rb._balance(shadows=(-20, 10, 25), midtones=(15, -8, -12), highlights=(10, 5, -20))
    loose = rb._balance(shadows=(-20, 10, 25), midtones=(15, -8, -12), highlights=(10, 5, -20))
    loose["colorBalanceSettings"]["preserveLuminosity"] = False
    yield "color balance, luminosity free", loose
    yield "black & white, tinted", rb.adjustment("Black & White", blackWhiteSettings={
        "reds": 120, "yellows": -50, "greens": 40, "cyans": 200, "blues": 20, "magentas": 80,
        "tint": True, "tintHue": 210, "tintSaturation": 35})
    yield "gradient map, reversed", rb.adjustment("Gradient Map", gradientMapSettings={
        "shadows": {"red": 0.1, "green": 0.15, "blue": 0.3}, "highlights": {"red": 1, "green": 0.9, "blue": 0.75}, "reversed": True})
    yield "grain", rb.adjustment("Grain", grainSettings={"amount": 60, "size": 0.7, "roughness": 80, "seed": 4000000000})
    yield "tonal contrast, defaults", rb.tonal_contrast()
    yield "tonal contrast, radius 60", rb.tonal_contrast(amount=70, radius=60, shadows=20, midtones=80, highlights=-10)
    yield "tonal contrast, radius 100", rb.tonal_contrast(amount=50, radius=100, shadows=50, midtones=60, highlights=40)
    yield "sharpen, defaults", rb.sharpen()
    yield "sharpen, radius 2.7, 180%", rb.sharpen(amount=180, radius=2.7)
    for key, (label, layers) in rb.LOOKS.items():
        for name, settings, _ in layers:
            yield f"look {key}: {name}", settings


def bench_adjust(binary):
    import colorsys
    import tempfile
    hues = np.linspace(0, 1, 360, endpoint=False)
    sweep = np.array([[colorsys.hls_to_rgb(h, l, s) for h in hues] for l in np.linspace(0.02, 0.98, 25)
                      for s in (0.15, 0.5, 1.0)])
    colors = np.vstack([(sweep * 255).round().astype(np.uint8),
                        np.random.default_rng(7).integers(0, 256, (120, 360, 3), dtype=np.uint8)])
    photo = portrait("collins_orig.jpg")["rgb8"][900:1400, 700:1200]
    # Tonal Contrast and Sharpen blur in Core Image, which works at reduced precision: its blur comes out a few tenths
    # of a level low, more on noise, and within three radii of the edges, where Tonal Contrast's unclamped base is
    # thin and 8-bit, the two drift apart more. They are held to a looser ceiling, away from the edges, on a smooth
    # sweep and a larger crop of the photo big enough to have an inside at radius 100; the edge band is reported.
    core_image = {"Tonal Contrast", "Sharpen"}
    wide_sweep = np.tile((sweep * 255).round().astype(np.uint8), (15, 3, 1))
    big_photo = portrait("collins_orig.jpg")["rgb8"][600:1700, 400:1500]
    worst, differing, ci_worst, ci_over, ci_edge = 0, 0.0, 0, 0.0, 0
    with tempfile.TemporaryDirectory() as folder:
        for name, settings in adjustment_cases():
            with open(os.path.join(folder, "a.json"), "w") as f:
                json.dump(settings, f)
            row = []
            blurs = settings["kind"] in core_image
            for image in (wide_sweep, big_photo) if blurs else (colors, photo):
                height, width = image.shape[:2]
                np.dstack([image, np.full((height, width), 255, np.uint8)]).tofile(os.path.join(folder, "in.rgba"))
                subprocess.run([binary, os.path.join(folder, "a.json"), str(width), str(height), os.path.join(folder, "in.rgba"),
                                os.path.join(folder, "out.rgba")], check=True)
                reference = np.fromfile(os.path.join(folder, "out.rgba"), np.uint8).reshape(height, width, 4)[..., :3]
                ours = rb.to8(rb.adjusted(image.astype(np.float32) / 255.0, settings))
                difference = np.abs(reference.astype(int) - ours.astype(int))
                if blurs:
                    reach = rb.sampling_margin(settings)
                    inside = difference[reach:-reach, reach:-reach]
                    ci_worst, ci_over = max(ci_worst, int(inside.max())), max(ci_over, 100 * (inside.max(-1) > 1).mean())
                    ci_edge = max(ci_edge, int(difference.max()))
                    row.append(f"max {int(inside.max())}, {100 * (inside.max(-1) > 1).mean():.3f}% over 1 (edge band max {int(difference.max())})")
                else:
                    worst, differing = max(worst, int(difference.max())), max(differing, 100 * (difference.max(-1) > 0).mean())
                    row.append(f"max {int(difference.max())}, {100 * (difference.max(-1) > 0).mean():.3f}% differ")
            print(f"  {name[:46]:46s} colors: {row[0]:22s} photo: {row[1]}")
    print(f"  WORST  {check('max difference (levels)', worst, ceiling=1)}  {check('pixels differing %', differing, ceiling=0.2)}")
    print(f"  WORST  Core Image blurs, away from the edges: {check('max difference (levels)', ci_worst, ceiling=3)}  "
          f"{check('pixels over 1 level %', ci_over, ceiling=0.5)}  (edge band max {ci_edge})")


if __name__ == "__main__":
    started = time.time()
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    benches = {"skin": bench_skin, "smoothing": bench_smoothing, "light": bench_light}
    if which == "cg":
        bench_cg(sys.argv[2], sys.argv[3])
    elif which == "adjust":
        bench_adjust(sys.argv[2])
    elif which == "lightcheck":
        light_projects(sys.argv[2])
    elif which in benches:
        benches[which]()
    else:
        sys.exit(__doc__)
    print(f"  ({time.time() - started:.0f}s) " + (f"BAD: {', '.join(failures)}" if failures else "all within the recorded floors"))
    sys.exit(1 if failures else 0)
