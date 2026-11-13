"""Checks for pcraft_dodge_burn.py. Command line only: photocraft-cli mcp has no window.
Usage: check.py WORKDIR PHOTO [SUITE...]     suites: pin plan brush look paint lines curves guards document encoding"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys

import cv2
import numpy as np

TESTS = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(TESTS), "scripts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import pcraft_dodge_burn as D  # noqa: E402
import photocraft_mcp  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "pcraft_dodge_burn.py")
FULL = "skin=clear,circles=clear,folds=clear"
failures = []


def ok(condition, label):
    print(("  ok: " if condition else "  BAD: ") + label, flush=True)
    if not condition:
        failures.append(label)


def run(work, *argv, expect=0, env=None):
    result = subprocess.run([sys.executable, SCRIPT, *argv], capture_output=True, encoding="utf-8", errors="replace",
                            cwd=work, env=env)
    if result.returncode != expect:
        print(result.stdout[-2500:], result.stderr[-2500:])
    shown = " ".join(a for a in argv if not a.startswith("/"))[:80]
    ok(result.returncode == expect, f"pcraft_dodge_burn {shown} exits {expect}")
    return result.stdout


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def read(path):
    return cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)


def lum(image):
    image = image.astype(np.float64)
    return image[..., 0] * 0.11 + image[..., 1] * 0.59 + image[..., 2] * 0.3


def change(result, original):
    """How far the lightness moved, in levels, read broadly: a JPEG written again differs from itself by a few
    levels pixel to pixel, which this averages away."""
    return cv2.GaussianBlur(lum(result) - lum(original), (0, 0), 4.0)


def layer_names(pcraft):
    cli = photocraft_mcp.find_cli(print, print)
    info = json.loads(subprocess.run([cli, "info", pcraft], capture_output=True, encoding="utf-8", check=True).stdout)
    return [(layer.get("name"), layer.get("kind")) for layer in info.get("layers", [])]


def table(text):
    """{item: (measured, measured again or None, need)} from the script's table."""
    found = {}
    for line in text.splitlines():
        match = re.match(r"^\s{5}(\w+)\s+([\d.]+)(?: ->\s*([\d.]+))?\s+(?:auto: )?(clear|some|none)\b", line)
        if match:
            found[match.group(1)] = (float(match.group(2)), float(match.group(3)) if match.group(3) else None, match.group(4))
    return found


def landmarks(photo):
    engine = D.Engine()
    try:
        staged = engine.path("portrait" + os.path.splitext(photo)[1])
        shutil.copyfile(photo, staged)
        engine.session.call("doc_open", {"path": staged})
        return engine.session.command("image.detectFaces", {})["faces"][0]["landmarks"]
    finally:
        engine.close()


# ---------------------------------------------------------------- pin

def suite_pin(work, photo):
    for name in ("photocraft-retouch", "photocraft-liquify"):
        other = os.path.join(os.path.dirname(os.path.dirname(TESTS)), name, "scripts",
                             "photocraft.py" if name.endswith("retouch") else "photocraft_mcp.py")
        if not os.path.isfile(other):
            print(f"  ({name} is not beside this skill: nothing to compare)")
            continue
        text = open(other, encoding="utf-8").read()
        ok(f'VERSION = "{photocraft_mcp.VERSION}"' in text and photocraft_mcp.ZIP_SHA256 in text,
           f"the photocraft-cli pin equals {name}'s")


# ---------------------------------------------------------------- plan (no engine)

def a_face(tilt=0.0, centre=(600.0, 500.0), apart=200.0):
    """Five landmarks of a face looking at the camera, its head tilted by `tilt` degrees."""
    c, s = math.cos(math.radians(tilt)), math.sin(math.radians(tilt))

    def at(x, y):
        return [centre[0] + apart * (x * c - y * s), centre[1] + apart * (x * s + y * c)]
    return D.Face({"rightEye": at(-0.5, 0), "leftEye": at(0.5, 0), "nose": at(0, 0.6), "mouthRight": at(-0.4, 1.05),
                   "mouthLeft": at(0.4, 1.05)})


def suite_plan(work, photo):
    spec = D.parse_needs("skin=some, folds=none, forehead=clear, lines=some, crows=none")
    ok(spec["cheeks"] == spec["chin"] == "some" and spec["forehead"] == "clear" and spec["folds"] == "none"
       and spec["frown"] == spec["marionette"] == spec["forehead_lines"] == "some" and spec["crows"] == "none"
       and spec["circles"] == spec["troughs"] == spec["shine"] == spec["shape"] == "auto",
       "needs: a group sets its items, a later word overrides an earlier one, what is not said is auto")
    spec = D.parse_needs("all=none,forehead-lines=clear")
    ok(all(spec[item] == "none" for item in D.ITEMS if item not in ("forehead_lines", "shape")) and spec["forehead_lines"] == "clear"
       and spec["shape"] == "auto", "`all` is every item but the modelling; a dash reads as an underscore")
    try:
        D.parse_needs("skin=lots")
        ok(False, "a need that is no need is refused")
    except D.Fail as error:
        ok("clear, some, none, auto" in str(error) and "forehead_lines" in str(error),
           "a need that is no need is refused, saying what the items and needs are")
    ok([D.auto_need("cheeks", v) for v in (1.0, 3.0, 9.0)] == ["none", "some", "some"]
       and [D.auto_need("circles", v) for v in (2.0, 8.0, 15.0, 25.0)] == ["none", "some", "some", "none"]
       and D.auto_need("shape", 99.0) == "none" and all(D.auto_need(item, 200.0) != "clear" for item in D.ITEMS),
       "auto: nothing under the threshold, half above it and never all; under the eyes none again once it is too dark to "
       "be a dark circle; modelling never")
    for tilt in (0.0, 25.0, 180.0):
        face = a_face(tilt)
        ok(abs(face.apart - 200) < 0.01 and float((face.mouth - face.eyes) @ face.down) > 0
           and abs(float(face.across @ face.down)) < 1e-5, f"a face tilted {tilt:g} deg: down points from the eyes to the mouth")
    face = a_face(15.0)
    placed = D.zones(face, (1100, 1200))
    for one, eye in zip(placed["eyes"], (face.eye_a, face.eye_b)):
        ys, xs = np.nonzero(one["zone"])
        below = (xs - eye[0]) * face.down[0] + (ys - eye[1]) * face.down[1]
        ys, xs = np.nonzero(one["corner"])
        outside = ((xs - eye[0]) * face.across[0] + (ys - eye[1]) * face.across[1]) * one["side"]
        ok(below.mean() > 0.2 * face.eye and one["zone"][int(eye[1]), int(eye[0])] == 0
           and (one["ring"] > 0).sum() > 200 and not ((one["ring"] > 0) & (one["zone"] > 0)).any()
           and len(xs) > 200 and outside.min() > 0.3 * face.eye and (one["trough"] > 0).sum() > 200,
           "an eye: the shade's zone below it with skin round it to compare with, a zone for the line under the bag, "
           "one for the crow's feet outside its outer corner")
    ys, xs = np.nonzero(placed["folds"] > 0.5)
    along = (xs - face.eyes[0]) * face.down[0] + (ys - face.eyes[1]) * face.down[1]
    ok(len(xs) > 500 and along.min() > 0.3 * face.drop and along.max() < 1.6 * face.drop
       and placed["guard"][int(face.nose[1]), int(face.nose[0])] > 0 and placed["guard"][int(face.mouth[1]), int(face.mouth[0])] > 0
       and placed["features"][int(face.nose[1]), int(face.nose[0])] == 0,
       "the smile lines run from beside the nose to past the mouth; nose and mouth are kept out of the skin to even, "
       "the nose stays in the skin that can shine")
    areas = placed["areas"]
    up, down = face.eyes - face.down * 1.2 * face.eye, face.mouth + face.down * 0.7 * face.drop
    cheek, brows = face.eye_a + face.down * 1.0 * face.eye, face.eyes - face.down * 0.4 * face.eye
    ok(areas["forehead"][int(up[1]), int(up[0])] and areas["chin"][int(down[1]), int(down[0])]
       and areas["cheeks"][int(cheek[1]), int(cheek[0])] and not (areas["forehead"] & areas["cheeks"]).any()
       and not (areas["cheeks"] & areas["chin"]).any() and placed["frown"][int(brows[1]), int(brows[0])] > 0,
       "forehead, cheeks and chin are three areas that do not overlap; between the brows is where a frown is looked for")
    # dabs: a wanted map of soft bumps is followed by a few hundred dabs
    rng = np.random.default_rng(3)
    wanted = np.zeros((500, 500), np.float32)
    yy, xx = np.mgrid[0:500, 0:500]
    for _ in range(40):
        x, y, height = rng.integers(60, 440), rng.integers(60, 440), rng.uniform(-0.03, 0.03)
        wanted += (height * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * 9.0 ** 2))).astype(np.float32)
    flat = np.full((500, 500, 3), 0.6, np.float32)
    dabs = D.dabs_for(wanted, flat, (12.0, 6.0, 3.0), 200, D.CAP, "gray", "cheeks")
    painted = np.zeros_like(wanted)
    for dab in dabs:
        (x, y), sigma = dab["points"][0], dab["size"] * D.DAB_SIGMA
        painted += dab["lift"] * np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / (2 * sigma * sigma))
    left = float(np.abs(wanted - painted).mean() / np.abs(wanted).mean())
    ok(0 < len(dabs) <= 200 and left < 0.6 and max(abs(d["lift"]) for d in dabs) <= D.CAP + 1e-6
       and all(d["opacity"] <= 0.35 and d["item"] == "cheeks" for d in dabs),
       f"{len(dabs)} dabs follow a map of soft patches ({100 * (1 - left):.0f}% of it), none past the cap")
    ok(D.dabs_for(np.zeros((300, 300), np.float32), flat[:300, :300], (12.0,), 50, D.CAP, "gray", "chin") == [],
       "nothing wanted, nothing painted")
    up, down = D.soft_light_reach(flat)
    ok(abs(D.opacity_for(0.03, flat, np.ones((500, 500), bool), "gray") - 0.03 / float(up.mean())) < 1e-6
       and abs(D.opacity_for(-0.03, flat, np.ones((500, 500), bool), "gray") - 0.03 / float(down.mean())) < 1e-6,
       "the brush opacity for a lift is the lift over what white can do there; for a lowering, over what black can")
    # the line finder on a plain patch of skin: lines that fan out from one point (crow's feet) come out as lines
    eye = 100.0
    y = np.full((400, 500), 0.6, np.float32) + rng.normal(0, 0.004, (400, 500)).astype(np.float32)
    skin = np.ones((400, 500), np.float32)
    zone = np.zeros((400, 500), np.uint8)
    zone[60:340, 60:440] = 255
    signed = lambda image: cv2.GaussianBlur(image, (0, 0), 0.35 * eye) - cv2.GaussianBlur(image, (0, 0), 0.04 * eye)
    none = D.find_creases(y, signed(y), skin, skin, zone, eye, (1.0, 0.0), 55, 3, 0.22 * eye)
    yy, xx = np.mgrid[0:400, 0:500].astype(np.float32)
    for slope in (-0.45, 0.0, 0.45):  # three lines from (120, 200), 60 levels of the 255 deep before any blur
        t = np.clip(xx - 120.0, 0, 260.0)
        y -= (0.09 * np.exp(-(np.hypot(xx - (120.0 + t), yy - (200.0 + slope * t)) ** 2) / (2 * 3.5 ** 2))).astype(np.float32)
    lines = D.find_creases(y, signed(y), skin, skin, zone, eye, (1.0, 0.0), 55, 3, 0.22 * eye)
    ends = sorted(round(float(line["points"][np.argmax(line["points"][:, 0])][1])) for line in lines)
    ok(none == [] and len(lines) == 3 and ends[0] < 130 and 180 < ends[1] < 220 and ends[2] > 270
       and all(4 < 255 * float(np.median(line["stands"])) < D.NOT_A_CREASE for line in lines),
       f"three lines fanning out from one point are found as three lines, none on plain skin (they end at y {ends})")
    across = D.find_creases(y, signed(y), skin, skin, zone, eye, (0.0, 1.0), 25, 3, 0.22 * eye)
    ok(across == [], "and a line is only found by the item whose direction it runs in")
    # a line is a crease only on skin, with skin close on both its sides, and when it is not too dark to be skin
    weight = np.zeros((300, 300), np.float32)
    weight[:, 60:240] = 1.0
    line = np.stack([np.full(12, 150.0), np.linspace(60, 240, 12)], axis=1).astype(np.float32)
    edge = np.stack([np.full(12, 64.0), np.linspace(60, 240, 12)], axis=1).astype(np.float32)
    faint, plain, black = (np.full(12, level / 255.0, np.float32) for level in (1.0, 12.0, 60.0))
    ok(D.a_crease(weight, line, plain, 60.0) and not D.a_crease(weight, line, faint, 60.0)
       and not D.a_crease(weight, line, black, 60.0) and not D.a_crease(weight, edge, plain, 60.0)
       and D.a_crease(weight, edge, plain, 60.0, sides=False),
       "a crease: a line that shows, on skin with skin on both sides; not one too faint, too dark (a hair), or along "
       "the skin's edge")


# ---------------------------------------------------------------- the brush itself (engine)

def suite_brush(work, photo):
    """The numbers the strokes are planned with, measured again on this PhotoCraft: a soft dab's height and width, a
    line's, what a second stroke adds."""
    engine = D.Engine()
    try:
        session = engine.session
        session.call("doc_new", {"width": 1600, "height": 900, "background": "white"})
        session.command("edit.fill", {"contents": "gray"})
        base = lum((engine.shot() * 255))
        session.command("paint.stroke", {"points": [[300, 300]], "size": 200, "hardness": 0, "opacity": 0.4, "flow": 1.0, "color": "#ffffff"})
        session.command("paint.stroke", {"points": [[700, 300], [1300, 300]], "size": 200, "hardness": 0, "opacity": 0.3, "flow": 1.0, "color": "#000000"})
        for _ in range(2):
            session.command("paint.stroke", {"points": [[700, 650], [1300, 650]], "size": 200, "hardness": 0, "opacity": 0.3, "flow": 1.0, "color": "#ffffff"})
        after = lum(engine.shot() * 255) - base
    finally:
        engine.close()
    peak = after[300, 300]
    profile = [after[300, 300 + int(t * 100)] / peak for t in (0.5, 1.0)]
    sigma = 200 * D.DAB_SIGMA
    model = [math.exp(-((t * 100) ** 2) / (2 * sigma * sigma)) for t in (0.5, 1.0)]
    ok(abs(peak - 0.4 * 127) < 3 and all(abs(p - m) < 0.06 for p, m in zip(profile, model)),
       f"a soft dab: opacity x the way to white at its middle ({peak:.0f} of 51), a Gaussian {D.DAB_SIGMA} sizes wide "
       f"({profile[0]:.2f}, {profile[1]:.2f} at half and one radius)")
    ok(abs(after[300, 1000] + 0.3 * 128) < 3 and abs(after[300, 850] - after[300, 1150]) < 2,
       f"a line: its opacity all along ({after[300, 1000]:.0f} of -38), however the dabs in it overlap")
    ok(abs(after[650, 1000] - (1 - 0.7 ** 2) * 127) < 4, f"a second stroke adds on top of the first ({after[650, 1000]:.0f} of 65)")


# ---------------------------------------------------------------- look and paint (engine)

def suite_look(work, photo):
    folder = os.path.join(work, "look")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    before = sha(mine)
    text = run(folder, "look", mine)
    stem = os.path.join(folder, "portrait_光影")
    found = table(text)
    ok(set(found) == set(D.ITEMS) - {"shape"} and "shape" in text and "[SUCCESS] measured only" in text
       and found["cheeks"][0] > 1 and found["circles"][0] > 2 and "picture's left" in text,
       f"look prints every item with its measurement, its two sides and what auto makes of it ({len(found)} items)")
    pictures = sorted(name for name in os.listdir(stem + ".checks"))
    picture = read(os.path.join(stem + ".checks", "look.jpg"))
    ok(pictures == ["look.jpg", "look_eyes.jpg", "look_forehead.jpg", "look_mouth.jpg"] and text.count("[READ]") == 4
       and picture.shape[1] > 1.6 * picture.shape[0],
       f"and writes the face and three close-ups, each the check view beside the zones ({', '.join(pictures)})")
    ok(sha(mine) == before and not os.path.exists(stem + ".jpg") and not os.path.exists(stem + ".pcraft"),
       "nothing is painted and the photo is as it was")
    flat = os.path.join(folder, "flat.png")
    cv2.imencode(".png", np.full((600, 800, 3), (140, 130, 120), np.uint8))[1].tofile(flat)
    for command in (["look", flat], ["apply", flat, "--needs", FULL]):
        text = run(folder, *command)
        ok("no face found" in text and "[SUCCESS]" in text and not os.path.exists(os.path.join(folder, "flat_光影.jpg")),
           f"{command[0]} on a picture without a face says so and writes no result")


def suite_paint(work, photo):
    folder = os.path.join(work, "paint")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    before = sha(mine)
    text = run(folder, "apply", mine, "--needs", "all=clear")
    stem = os.path.join(folder, "portrait_光影")
    ok("[SUCCESS]" in text and all(os.path.isfile(stem + ext) for ext in (".jpg", ".pcraft", ".light.json")),
       "apply writes the .jpg, the .pcraft and its record beside the photo")
    ok(sha(mine) == before, "the photo itself is untouched")
    if not os.path.isfile(stem + ".jpg"):
        return
    names = layer_names(stem + ".pcraft")
    ok(len(names) == 2 and names[0][0].startswith("🩶 中性灰 · AI 光影 (") and "脸颊不匀 全量" in names[0][0]
       and "黑眼圈 全量" in names[0][0] and "立体感" not in names[0][0] and names[0][1] == "Pixel" and names[1] == ("原图", "Pixel"),
       f"the document: the photo untouched below, one gray layer named by what it painted above ({names[0][0]})")
    found = table(text)
    down = {item: 1 - found[item][1] / found[item][0] for item in ("cheeks", "chin", "circles", "folds") if found[item][0] > 1}
    ok(len(down) == 4 and all(0.12 < share < 0.6 for share in down.values()),
       "measured again on the same skin and lines, cheeks, chin, under the eyes and the smile lines have each come down "
       f"by a part, not by all ({ {k: round(100 * v) for k, v in down.items()} } %)")
    record = json.load(open(stem + ".light.json", encoding="utf-8"))["faces"][0]["by_item"]
    for item in ("circles", "folds"):
        row = record[item]
        ok(row["strokes"] in (1, 2) and 0.6 * row["meant"] <= row["landed"] <= 1.6 * row["meant"],
           f"{item}: a stroke a side, landing where it was meant ({row['landed']} of {row['meant']} levels)")
    ok(20 <= record["cheeks"]["dabs"] <= D.DABS["cheeks"] and 0.4 < record["cheeks"]["share"] < 1.3,
       f"cheeks: {record['cheeks']['dabs']} dabs paint {100 * record['cheeks']['share']:.0f}% of what was wanted")
    original, result = read(mine).astype(int), read(stem + ".jpg").astype(int)
    moved = np.abs(change(result, original))
    faces = np.zeros(moved.shape, bool)
    face_rows, face_columns = np.nonzero(moved > 2)
    faces[face_rows.min():face_rows.max() + 1, face_columns.min():face_columns.max() + 1] = True
    ok(result.shape == original.shape and moved.max() <= 255 * D.CAP * 1.6 and moved[~faces].max() <= 2
       and faces.mean() < 0.12,
       f"the picture changed on the face only ({100 * faces.mean():.0f}% of the frame), nowhere by more than the cap "
       f"allows (most {moved.max():.0f} levels)")
    checks = sorted(name for name in os.listdir(stem + ".checks") if name.endswith(".jpg"))
    ok({"light.jpg", "light_bw.jpg", "light_map.jpg", "light_eyes.jpg", "light_mouth.jpg"} <= set(checks)
       and text.count("[READ]") == len(checks),
       f"the face and a close-up of each part that was painted are written and listed to read ({', '.join(checks)})")
    text = run(folder, "apply", mine, "--needs", "all=none,skin=some,circles=some")
    milder = np.abs(change(read(stem + ".jpg").astype(int), original))
    name = layer_names(stem + ".pcraft")[0][0]
    ok(milder[faces].sum() < 0.75 * moved[faces].sum() and "法令纹" not in name and "脸颊不匀 一半" in name,
       "run again at lower needs: its own result is replaced by a milder one, made from the photo again")
    text = run(folder, "apply", mine, "--needs", "all=none", "--out", os.path.join(folder, "none.jpg"))
    ok("[SUCCESS] nothing to paint" in text and len(table(text)) == len(D.ITEMS) - 1 and not os.path.exists(os.path.join(folder, "none.jpg")),
       "every need none is a result: the measurements said, and no file written")
    text = run(folder, "apply", mine, "--needs", "all=none,shape=clear", "--out", os.path.join(folder, "shape.jpg"))
    record = json.load(open(os.path.join(folder, "shape.light.json"), encoding="utf-8"))["faces"][0]["by_item"]
    shaped = change(read(os.path.join(folder, "shape.jpg")).astype(int), original)
    ok(list(record) == ["shape"] and record["shape"]["strokes"] >= 4 and 1.5 < shaped.max() < 9 and -9 < shaped.min() < -1,
       f"modelling alone, when asked for: a few broad strokes that lift ({shaped.max():.1f}) and lower ({shaped.min():.1f}) a little")
    # the five points by hand
    faces_file = os.path.join(folder, "faces.json")
    json.dump({"faces": [landmarks(mine)]}, open(faces_file, "w"))
    text = run(folder, "look", mine, "--faces", faces_file, "--out", os.path.join(folder, "byhand.jpg"))
    ok(len(table(text)) == len(D.ITEMS) - 1 and os.path.isfile(os.path.join(folder, "byhand.checks", "look.jpg")),
       "a face given by its five points is measured like one PhotoCraft found")
    json.dump({"faces": [{"rightEye": [1, 2]}]}, open(faces_file, "w"))
    text = run(folder, "look", mine, "--faces", faces_file, expect=1)
    ok("needs rightEye, leftEye, nose, mouthRight and mouthLeft" in text, "a face missing points is refused with what it needs")


def suite_lines(work, photo):
    """Creases the test portrait does not have, drawn on it: a frown line, crow's feet, a line under an eye bag and
    one below a corner of the mouth. They must be found where they are, lightened, and nothing else taken for one."""
    folder = os.path.join(work, "lines")
    os.makedirs(folder)
    face = D.Face(landmarks(photo))
    image = read(photo).astype(np.float32)
    eye, across, down = face.eye, face.across, face.down

    def draw(start, end, depth=24.0):
        """A soft dark line from `start` to `end`, `depth` levels deep, as wide as a crease."""
        yy, xx = np.mgrid[0:image.shape[0], 0:image.shape[1]].astype(np.float32)
        along = np.array(end, np.float32) - np.array(start, np.float32)
        length = float(np.hypot(*along))
        along /= length
        t = np.clip((xx - start[0]) * along[0] + (yy - start[1]) * along[1], 0, length)
        distance = np.hypot(xx - (start[0] + t * along[0]), yy - (start[1] + t * along[1]))
        image[...] -= (depth * np.exp(-distance ** 2 / (2 * (0.035 * eye) ** 2)))[..., None]

    between = face.eyes - down * 0.38 * eye
    draw(between - down * 0.3 * eye, between + down * 0.3 * eye)                                   # a frown line
    # Crow's feet: beside the outer corner of whichever eye has the more skin there (hair covers a temple).
    height, width = image.shape[:2]
    box = face.box(width, height)
    skin = D.measure(image[box] / 255.0, face.shifted(-box[1].start, -box[0].start))["wide_weight"]
    offset = np.array([box[1].start, box[0].start], np.float32)
    reach = {}
    for centre, side in ((face.eye_a, -1.0), (face.eye_b, 1.0)):
        outer = centre + across * side * 0.72 * eye
        clear = [float(D.at(skin, np.array([outer + across * side * far * eye - offset], np.float32))[0]) > 0.8
                 for far in np.linspace(0.05, 0.6, 12)]
        reach[side] = (clear.index(False) if False in clear else len(clear)) * 0.05
    side = max(reach, key=reach.get)
    long = max(0.26, min(0.45, reach[side] - 0.04))
    corner = (face.eye_a if side < 0 else face.eye_b) + across * side * 0.72 * eye
    draw(corner + across * side * 0.05 * eye, corner + across * side * long * eye - down * 0.1 * eye)
    draw(corner + across * side * 0.05 * eye + down * 0.2 * eye, corner + across * side * long * eye + down * 0.38 * eye)
    under = face.eye_b + down * 0.52 * eye
    draw(under - across * 0.4 * eye - down * 0.05 * eye, under + across * 0.4 * eye)                # under an eye bag
    below = face.mouth_a - across * 0.12 * eye + down * 0.35 * eye
    draw(below, below + down * 0.6 * eye - across * 0.05 * eye)                                     # below the mouth's corner
    mine = os.path.join(folder, "lined.png")
    cv2.imencode(".png", np.clip(image, 0, 255).astype(np.uint8))[1].tofile(mine)
    plain = table(run(folder, "look", photo, "--out", os.path.join(folder, "plain.jpg")))
    lined = table(run(folder, "look", mine))
    for item in ("frown", "troughs"):
        ok(lined[item][0] >= plain[item][0] + 3.0 and lined[item][2] == "some",
           f"{item}: the drawn line is found ({plain[item][0]} without it, {lined[item][0]} with) and auto takes half")
    # The line drawn below the mouth's corner runs on from where a smile line would: it is one or the other, not both.
    ok(lined["marionette"][0] + lined["folds"][0] >= plain["marionette"][0] + plain["folds"][0] + 3.0,
       f"below the mouth's corner: the drawn line is found, as a smile line or as the line below the corner (folds "
       f"{plain['folds'][0]} -> {lined['folds'][0]}, marionette {plain['marionette'][0]} -> {lined['marionette'][0]})")
    ok(lined["crows"][0] >= plain["crows"][0] - 1.0 and lined["crows"][0] >= 5.0,
       f"crow's feet: the portrait's own and the drawn ones are found ({plain['crows'][0]} -> {lined['crows'][0]})")
    ok(lined["forehead_lines"][0] == plain["forehead_lines"][0], "no line is found where none was drawn")
    text = run(folder, "apply", mine, "--needs", "all=none,frown=clear,crows=clear,troughs=clear,marionette=clear,folds=clear")
    found = table(text)
    for item in ("frown", "crows", "troughs", "folds"):
        ok(found[item][1] is not None and 0.2 * found[item][0] < found[item][1] < 0.88 * found[item][0],
           f"{item}: lightened along the line, which is still there ({found[item][0]} -> {found[item][1]})")
    ok("[WARN]" not in text, "and no place was painted twice over")
    original, result = read(mine).astype(int), read(os.path.join(folder, "lined_光影.jpg")).astype(int)
    lifted = change(result, original)
    ok(lifted.max() > 1.5 and lifted.min() > -2.5, "only lifting strokes were painted")


def suite_curves(work, photo):
    folder = os.path.join(work, "curves")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    text = run(folder, "apply", mine, "--needs", "all=clear", "--on", "curves")
    stem = os.path.join(folder, "portrait_光影")
    if not os.path.isfile(stem + ".pcraft"):
        ok(False, "the pass on curves writes its document")
        return
    names = layer_names(stem + ".pcraft")
    ok(len(names) == 3 and names[0][0].startswith("☀️ 双曲线 · 提亮 · AI 光影") and names[1][0].startswith("🌑 双曲线 · 压暗 · AI 光影")
       and [kind for _, kind in names] == ["Adjustment", "Adjustment", "Pixel"],
       f"on curves: a brightening and a darkening Curves layer over the photo ({[name[:14] for name, _ in names]})")
    found = table(text)
    ok(all(found[item][1] < found[item][0] for item in ("cheeks", "chin", "circles", "folds")),
       "measured again, cheeks, chin, under the eyes and the smile lines have each come down")
    record = json.load(open(stem + ".light.json", encoding="utf-8"))["faces"][0]["by_item"]
    for item in ("circles", "folds"):
        row = record[item]
        ok(0.6 * row["meant"] <= row["landed"] <= 1.6 * row["meant"],
           f"{item} through the curve's mask lands where it was meant ({row['landed']} of {row['meant']} levels)")
    original, result = read(mine).astype(int), read(stem + ".jpg").astype(int)
    hue = lambda image: cv2.cvtColor(image.astype(np.uint8), cv2.COLOR_BGR2HSV)[..., 0].astype(int)
    lifted = change(result, original) > 3
    turn = np.abs(hue(result) - hue(original))[lifted]
    ok(lifted.sum() > 500 and np.median(np.minimum(turn, 180 - turn)) <= 1,
       "in Luminosity the lifted skin keeps its hue")


# ---------------------------------------------------------------- guards and documents (engine)

def suite_guards(work, photo):
    folder = os.path.join(work, "guards")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    before = sha(mine)
    stem = os.path.join(folder, "portrait_光影")
    text = run(folder, "apply", mine, "--needs", FULL, "--out", mine, expect=1)
    ok("never replaced" in text and sha(mine) == before, "--out naming the photo is refused; the photo is as it was")
    text = run(folder, "apply", mine, "--needs", "skin=much", expect=1)
    ok("[Error]" in text and not os.path.exists(stem + ".jpg"), "a bad need stops before anything is written")
    open(stem + ".jpg", "wb").write(b"someone else's file")
    text = run(folder, "apply", mine, "--needs", FULL, expect=1)
    ok("not as this script last wrote it" in text and open(stem + ".jpg", "rb").read() == b"someone else's file",
       "a file already at the result's place is not replaced")
    run(folder, "apply", mine, "--needs", FULL, "--force")
    run(folder, "apply", mine, "--needs", FULL)
    with open(stem + ".pcraft", "ab") as handle:
        handle.write(b"edited")
    edited = sha(stem + ".pcraft")
    text = run(folder, "apply", mine, "--needs", FULL, expect=1)
    ok("not as this script last wrote it" in text and sha(stem + ".pcraft") == edited,
       "a .pcraft changed since it was written (the user painted on it) is not replaced without --force")
    run(folder, "apply", mine, "--needs", FULL, "--force")
    ok(sha(stem + ".pcraft") != edited and sha(mine) == before, "with --force it is; the photo still untouched")


def suite_document(work, photo):
    folder = os.path.join(work, "document")
    os.makedirs(folder)
    engine = D.Engine()
    try:  # a document with something above the photo: a curve that lightens it
        staged = engine.path("portrait.jpg")
        shutil.copyfile(photo, staged)
        engine.session.call("doc_open", {"path": staged})
        engine.session.command("layer.newAdjustmentLayer.curves", {"points": [[0, 0], [110, 150], [255, 255]]})
        engine.session.call("doc_save", {"path": engine.path("layered.pcraft")})
        engine.session.call("doc_close")
        mine = shutil.copy(engine.path("layered.pcraft"), os.path.join(folder, "layered.pcraft"))
    finally:
        engine.close()
    before = sha(mine)
    text = run(folder, "apply", mine, "--needs", FULL, "--out", os.path.join(folder, "layered.jpg"), expect=1)
    ok("never replaced" in text and sha(mine) == before, "a result whose .pcraft would be the document itself is refused")
    run(folder, "apply", mine, "--needs", FULL)
    stem = os.path.join(folder, "layered_光影")
    ok(sha(mine) == before and os.path.isfile(stem + ".pcraft"), "the document is not written; the result is a new one beside it")
    if not os.path.isfile(stem + ".pcraft"):
        return
    names = layer_names(stem + ".pcraft")
    ok(len(names) == 3 and "AI 光影" in names[0][0] and [kind for _, kind in names[1:]] == ["Adjustment", "Pixel"],
       f"its layers stay below, the pass is painted on top of what they show ({[name[:14] for name, _ in names]})")


# ---------------------------------------------------------------- a system whose own encoding is not UTF-8

def suite_encoding(work, photo):
    """A Chinese Windows reads pipes and prints in GBK. The engine answers in UTF-8, and the names here are Chinese:
    the run must not depend on the system's encoding. Stood in for by a GBK locale where the system has one."""
    locales = subprocess.run(["locale", "-a"], capture_output=True, encoding="utf-8", errors="replace").stdout.split()
    if "zh_CN.GBK" not in locales:
        print("  (no zh_CN.GBK locale on this system: not run)")
        return
    folder = os.path.join(work, "照片 工作")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "人像·棚拍（一）.jpg"))
    env = dict(os.environ, LC_ALL="zh_CN.GBK", LANG="zh_CN.GBK")
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    result = subprocess.run([sys.executable, SCRIPT, "apply", mine, "--needs", FULL], capture_output=True, cwd=folder,
                            env=env, timeout=300)
    try:
        text = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    stem = os.path.join(folder, "人像·棚拍（一）_光影")
    if result.returncode != 0:
        print(result.stdout[-1500:], result.stderr[-1500:])
    ok(result.returncode == 0 and all(os.path.isfile(stem + ext) for ext in (".jpg", ".pcraft", ".light.json")),
       "under a GBK locale, with Chinese file, folder and layer names, the run completes")
    ok(text is not None and "[SUCCESS]" in text and "人像·棚拍（一）_光影.jpg" in text, "and what it prints is UTF-8")
    names = layer_names(stem + ".pcraft") if os.path.isfile(stem + ".pcraft") else []
    ok(len(names) == 2 and names[0][0].startswith("🩶 中性灰 · AI 光影") and names[1][0] == "原图",
       f"and the layer names arrive whole ({[name[:14] for name, _ in names]})")


SUITES = {"pin": suite_pin, "plan": suite_plan, "brush": suite_brush, "look": suite_look, "paint": suite_paint,
          "lines": suite_lines, "curves": suite_curves, "guards": suite_guards, "document": suite_document,
          "encoding": suite_encoding}

if __name__ == "__main__":
    work, photo, wanted = sys.argv[1], sys.argv[2], sys.argv[3:] or list(SUITES)
    for name in wanted:
        if name not in SUITES:
            sys.exit(f"no suite {name}: {', '.join(SUITES)}")
        print(f"== {name}", flush=True)
        SUITES[name](work, photo)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    for label in failures:
        print("  BAD: " + label)
    sys.exit(1 if failures else 0)
