"""Checks for pcraft_liquify.py. Command line only: photocraft-cli mcp has no window.
Usage: check.py WORKDIR PHOTO [SUITE...]     suites: pin plan grid liquify captions tone guards"""
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

TESTS = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(TESTS), "scripts")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import pcraft_liquify as L  # noqa: E402
import photocraft_mcp  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "pcraft_liquify.py")
failures = []


def ok(condition, label):
    print(("  ok: " if condition else "  BAD: ") + label, flush=True)
    if not condition:
        failures.append(label)


def run(work, *argv, expect=0):
    result = subprocess.run([sys.executable, SCRIPT, *argv], capture_output=True, text=True, cwd=work)
    if result.returncode != expect:
        print(result.stdout[-2500:], result.stderr[-2500:])
    shown = " ".join(a for a in argv if not a.startswith("/"))[:80]
    ok(result.returncode == expect, f"pcraft_liquify {shown} exits {expect}")
    return result.stdout


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    return path


def fails(function, *args):
    try:
        function(*args)
    except L.Fail as error:
        return str(error)
    return None


# ---------------------------------------------------------------- pin

def suite_pin(work, photo):
    sibling = photocraft_mcp.sibling_module()
    if sibling is None:
        print("  (photocraft-retouch is not beside this skill: nothing to compare)")
        return
    ok(sibling.VERSION == photocraft_mcp.VERSION and sibling.ZIP_SHA256 == photocraft_mcp.ZIP_SHA256,
       "the photocraft-cli pin equals photocraft-retouch's")


# ---------------------------------------------------------------- plan (no engine)

def amounts(part, strength=1.0, long_side=5000):
    contours = L.part_contours(part, 0, strength, long_side)
    return [row[4] for contour in contours for row in contour["points"]], contours


def suite_plan(work, photo):
    L._warned.clear()
    straight = [[1000, 1000 + 80 * i] for i in range(9)]
    limb = dict(part="upper_arm", width=300, toward=[1150, 1300], edge=straight)
    values, contours = amounts(limb)
    ok(all(abs(v - 24) < 0.01 for v in values), "a straight upper arm moves 8% of its width everywhere")
    ok(all(row[2] > 0.99 and abs(row[3]) < 0.01 for c in contours for row in c["points"]),
       "pushes go toward `toward` (right of a vertical edge)")
    values, contours = amounts(dict(limb, toward=[850, 1300]))
    ok(all(row[2] < -0.99 for c in contours for row in c["points"]), "the other side's `toward` turns them round")
    bowed = [[1000 - 150 * math.sin(math.pi * i / 10), 1000 + 80 * i] for i in range(11)]
    values, _ = amounts(dict(limb, edge=bowed))
    ok(abs(values[0] - 24) < 0.5 and abs(values[-1] - 24) < 0.5 and 60 < max(values) <= 90,
       f"a bowed upper arm is flattened at the bulge (most {max(values):.0f} px of 300) and only inset at its ends")
    values, _ = amounts(dict(limb, edge=[[1000 - 400 * math.sin(math.pi * i / 10), 1000 + 80 * i] for i in range(11)]))
    ok(0.26 * 300 < max(values) <= 0.28 * 300, f"a limb's push levels off at the accepted most, however far it bows ({max(values):.0f} px of 300)")
    values, _ = amounts(dict(part="hip", width=100, toward=[1500, 1300], strength=1.5,
                             edge=[[1000, 1000 + 80 * i] for i in range(9)]), long_side=40)
    ok(max(values) <= 2.01 and any("held at" in w for w in L._warned),
       "no edge moves more than 5% of the picture or 30% of its part, and holding it back is said")
    jaw = dict(part="jaw", width=500, toward=[900, 1300], edge=[[1200, 1000 + 50 * i] for i in range(7)])
    values, contours = amounts(jaw)
    ok(abs(values[0] - 5.0) < 0.1 and abs(max(values) - 17.5) < 0.6 and values.index(max(values)) == 4
       and abs(values[-1] - 6.75) < 0.1, "jaw: 1% at the cheekbone, 3.5% at the jaw corner, 1.35% before the chin")
    ok(contours[0]["size"] == 200, "jaw brush = 0.40 x face width")
    waist = dict(part="waist", width=800, toward=[1500, 1400],
                 edge=[[1100 + 0.0004 * (y - 1400) ** 2, y] for y in range(1000, 2001, 100)])
    values, _ = amounts(waist)
    ok(values.index(max(values)) == 4 and abs(max(values) - 60) < 0.5 and values[0] < 0.6 * max(values)
       and 0.45 * max(values) < values[-1] < 0.85 * max(values),
       "waist: 7.5% at the point nearest `toward`, less above, about half toward the hip")
    elbow = dict(part="elbow", width=300, toward=[1000, 1000],
                 edge=[[1000 + 150 * math.cos(a), 1000 + 150 * math.sin(a)] for a in np.linspace(0, math.pi / 2, 6)])
    values, _ = amounts(elbow)
    ok(min(values) >= 25.4 and max(values) <= 28.7 and values.index(max(values)) in (2, 3),
       "elbow: the same small inset all round, a little more at the tip")
    values, _ = amounts(dict(jaw, strength=0.5))
    ok(abs(max(values) - 8.75) < 0.3, "strength 0.5 halves the push")
    plain, _ = amounts(dict(part="hip", width=1000, toward=[1500, 1400], edge=[[1000, 1000 + 60 * i] for i in range(9)]))
    eased, _ = amounts(dict(part="hip", width=1000, toward=[1500, 1400], edge=[[1000, 1000 + 60 * i] for i in range(9)],
                            ease_end=True))
    ok(abs(eased[-1] - 0.2 * plain[-1]) < 0.1 and abs(eased[0] - plain[0]) < 0.01, "ease_end runs the push out to a fifth")
    contours, nudges, dabs = L.read_plan({"parts": [dict(part="eye", at=[500, 500], width=100)]}, 1.0, 4000, 4000)
    ok(dabs == [dict(name="eye #1", tool="bloat", size=170, at=[500.0, 500.0], count=2, rate=80)],
       "an eye is two bloat dabs, brush 1.7 x its width")
    contours, _, _ = L.read_plan({"parts": [dict(part="eye", at=[500, 500], width=100), dict(jaw)]}, 1.0, 4000, 4000)
    ok(abs(max(row[4] for row in contours[0]["points"]) - 17.5) < 0.6, "an eye in the plan does not change the edges after it")
    for label, part, needle in (
            ("an unknown part", dict(part="chin", width=100, toward=[0, 0], edge=straight), "no part `chin`"),
            ("two edge points", dict(part="jaw", width=100, toward=[0, 0], edge=straight[:2]), "at least 3 points"),
            ("no `toward`", dict(part="upper_arm", width=100, edge=straight), "needs `toward`"),
            ("no width", dict(part="jaw", toward=[0, 0], edge=straight), "`width` must be"),
            ("strength 2", dict(part="jaw", width=100, toward=[0, 0], edge=straight, strength=2), "at most 1.8")):
        message = fails(L.part_contours, part, 0, 1.0, 4000)
        ok(message is not None and needle in message, f"{label} is refused with a reason")
    message = fails(L.read_plan, {"contours": [dict(name="x", size=300, direction=[1, 0],
                                                    points=[[1000, 1000, 400], [1000, 1200, 400]])]}, 1.0, 4000, 4000)
    ok(message is not None and "not a retouch" in message, "a push over 5% of the picture is refused")
    # `toward` close behind a strongly bowed edge (between the edge and its first-to-last line) still reads as a bulge
    values, _ = amounts(dict(limb, edge=bowed, toward=[930, 1400]))
    ok(60 < max(values) <= 90, f"the bulge is found wherever inside the limb `toward` sits (most {max(values):.0f} px)")
    hollow = [[1000 + 150 * math.sin(math.pi * i / 10), 1000 + 80 * i] for i in range(11)]
    values, _ = amounts(dict(limb, edge=hollow, toward=[1500, 1400]))
    ok(all(abs(v - 24) < 0.01 for v in values), "an edge that curves inward is only inset, never dug deeper")
    short, _ = amounts(dict(part="hip", width=1000, toward=[1500, 1100], edge=[[1000, 1000 + 30 * i] for i in range(9)],
                            ease_start=True, ease_end=True))
    ok(abs(short[4] - 26) < 0.01 and short[0] < 6, "easing both ends of a short edge leaves its middle at full strength")
    contours, _, _ = L.read_plan({"contours": [dict(name="edge", size=300, direction=[0.6, 0.8],
                                                    points=[[1000, 3990, 20], [1000, 3900, 20], [1000, 3700, 20]])]},
                                 1.0, 4000, 4000)
    rows = contours[0]["points"]
    ok(abs(rows[0][3]) < 0.08 and abs(rows[0][4] - 12) < 0.3 and abs(rows[2][3] - 0.8) < 0.01 and abs(rows[2][4] - 20) < 0.01,
       "at the picture's border a push keeps only its part along the border")
    for size, amount in ((640, 14.0), (300, 3.0), (100, 41.3)):
        stroke = L.drag(size, 1000, 1000, 1, 0, amount)
        length = stroke["points"][1][0] - stroke["points"][0][0]
        steps = length / (size * 0.025)
        ok(steps >= 2 and abs(steps - round(steps)) < 0.01 and abs(stroke["pressure"] / 100 * round(steps) * size * 0.025
                                                                   - amount) < 0.05,
           f"a {amount:g} px drag with a {size} px brush is whole steps, the pressure carrying the amount")
    L._warned.clear()


# ---------------------------------------------------------------- grid

def suite_grid(work, photo):
    image = np.full((400, 900, 3), 30, np.uint8)
    image[:, 512:] = 200
    cv2.imwrite(os.path.join(work, "step.png"), image)
    text = run(work, "probe", os.path.join(work, "step.png"), "--row", "200", "--from", "100", "--to", "800")
    found = [int(line.split("x=")[1].split(":")[0]) for line in text.splitlines() if line.strip().startswith("x=")]
    ok(found and abs(found[0] - 512) <= 3 and "dark to light" in text, f"probe finds a step in lightness where it is ({found[:1]})")
    out = os.path.join(work, "grid.jpg")
    text = run(work, "grid", photo, "--out", out, "--box", "500", "400", "1500", "1400")
    image = cv2.imread(out)
    ok(image is not None and max(image.shape[:2]) == 1500 and "grid every 100 px" in text, "grid writes a labelled view")


# ---------------------------------------------------------------- liquify (engine)

def layer_names(pcraft):
    cli = photocraft_mcp.find_cli(print, print)
    info = json.loads(subprocess.run([cli, "info", pcraft], capture_output=True, text=True, check=True).stdout)
    return [(layer.get("name"), layer.get("kind")) for layer in info.get("layers", [])]


def suite_liquify(work, photo):
    folder = os.path.join(work, "liquify")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    before = sha(mine)
    plan = write_json(os.path.join(folder, "plan.json"), {
        "parts": [dict(part="waist", name="waist left", width=900, toward=[1300, 1700],
                       edge=[[800 + 0.0003 * (y - 1700) ** 2, y] for y in range(1200, 2201, 100)]),
                  dict(part="eye", name="eye", at=[1300, 900], width=110)],
        "contours": [dict(name="tiny", size=600, direction=[1, 0],
                          points=[[2000, 2400, 2], [2000, 2550, 2], [2000, 2700, 2]])]})
    text = run(folder, "apply", mine, "--plan", plan)
    stem = os.path.join(folder, "portrait_液化")
    ok("[SUCCESS]" in text and all(os.path.isfile(stem + ext) for ext in (".jpg", ".pcraft", ".plan.json")),
       "apply writes the .jpg, the .pcraft and the plan beside the photo")
    ok(sha(mine) == before, "the photo itself is untouched")
    if not os.path.isfile(stem + ".jpg"):
        return
    report = json.load(open(os.path.join(stem + ".checks", "report.json")))
    edges = {edge["name"]: edge for edge in report["edges"]}
    waist = edges["waist left"]
    peak = waist["want"].index(max(waist["want"]))
    # Above the waist the recipe rises faster than a brush this size can follow: a few px over there is expected.
    ok(abs(waist["got"][peak] - waist["want"][peak]) <= 3 and waist["worst_miss"] <= 10,
       f"the waist edge lands where the recipe says: {waist['got'][peak]} of {waist['want'][peak]} px at the waist, "
       f"nowhere more than {waist['worst_miss']} px off")
    ok(all(1.4 <= got <= 2.8 for got in edges["tiny"]["got"]),
       f"a 2 px push with a 600 px brush is not dropped (got {edges['tiny']['got']})")
    original, result = cv2.imread(mine).astype(int), cv2.imread(stem + ".jpg").astype(int)
    ok(result.shape == original.shape, "the result has the photo's size")
    ok(np.abs(result[1400:2000, 700:1000] - original[1400:2000, 700:1000]).mean() > 2
       and np.abs(result[100:500, 2000:2500] - original[100:500, 2000:2500]).mean() < 1.5,
       "the picture changed at the edge and nowhere far from it")
    names = layer_names(stem + ".pcraft")
    ok(len(names) == 1 and names[0][1] == "Smart Object" and "液化" in names[0][0],
       f"the document is one Smart Object carrying the liquify ({names})")
    checks = sorted(os.listdir(stem + ".checks"))
    ok({"overview.jpg", "field.jpg", "report.json"} <= set(checks) and any(name[:2].isdigit() for name in checks)
       and not any(name.endswith(".plan.jpg") for name in checks), f"check pictures are written ({', '.join(checks)})")
    run(folder, "apply", mine, "--plan", stem + ".plan.json", "--strength", "0.7", "--no-pcraft")
    milder = cv2.imread(stem + ".jpg").astype(int)
    ok(0 < np.abs(milder - original)[1400:2000, 700:1000].mean() < np.abs(result - original)[1400:2000, 700:1000].mean(),
       "run again from the kept plan at --strength 0.7: its own result is replaced by a milder one")
    # A brush far too small for its push, and a joint with a stretch of outline left out before it.
    arm = dict(part="upper_arm", name="arm", width=300, toward=[1500, 1500], edge=[[1300, 900 + 80 * i] for i in range(6)])
    elbow = dict(part="elbow", name="elbow", width=280, toward=[1500, 1900],
                 edge=[[1300, 1800], [1310, 1880], [1360, 1950], [1440, 1990], [1520, 1990]])
    tight = dict(part="hip", name="tight", width=1500, toward=[2300, 2600], brush=120,
                 edge=[[1900, 2300 + 60 * i] for i in range(8)])
    text = run(folder, "apply", mine, "--no-pcraft", "--plan",
               write_json(os.path.join(folder, "second.json"), {"parts": [arm, elbow, tight]}))
    ok("a 120 px brush is small for a 39 px push" in text, "a brush too small for its push is said by name, before running")
    ok("the picture is stretched" in text and "beside `tight`" in text and "circled in field.jpg" in text,
       "and the measured stretch is reported with its place and the part that caused it")
    ok("of outline between the end of `arm` and `elbow` is not traced" in text, "outline left out before a joint is noted")
    views = [name for name in os.listdir(stem + ".checks") if name[:2].isdigit()]
    ok(views and not any("waist" in name or "tiny" in name for name in views) and text.count("[READ]") == len(views) + 2,
       f"check pictures of the earlier plan are gone, and every new one is listed to read ({', '.join(sorted(views))})")


# ---------------------------------------------------------------- captions (engine)

def suite_captions(work, photo):
    folder = os.path.join(work, "captions")
    os.makedirs(folder)
    image = cv2.imread(photo)
    cv2.putText(image, "STAND BY THE DOOR  03  SILHOUETTE", (500, 1750), cv2.FONT_HERSHEY_DUPLEX, 2.4, (255, 255, 255), 5,
                cv2.LINE_AA)
    mine = os.path.join(folder, "poster.png")
    cv2.imwrite(mine, image)
    caption = [dict(name="title", box=[480, 1670, 2200, 1780])]
    slanted = dict(name="slanted", size=420, direction=[1, 0],
                   points=[[700 + 0.9 * (y - 1400), y, 45] for y in range(1400, 2101, 100)])
    text = run(folder, "apply", mine, "--no-pcraft", "--plan",
               write_json(os.path.join(folder, "near.json"), {"contours": [slanted], "captions": caption}))
    row = json.load(open(os.path.join(folder, "poster_液化.checks", "report.json")))["captions"][0]
    ok("The text will look bent" in text and row["lean_share"] > 6, f"a slanted edge through text is reported "
       f"(lean up to {row['lean_max']} deg, {row['lean_share']}% of glyphs over 3)")
    ok(os.path.isfile(os.path.join(folder, "poster_液化.checks", "captions.jpg")), "and shown in captions.jpg")
    far = dict(slanted, points=[[x, y - 900 if y > 1700 else y - 1100, px] for x, y, px in slanted["points"]])
    far["points"] = [[700, 300 + 100 * i, 45] for i in range(6)]
    text = run(folder, "apply", mine, "--no-pcraft", "--plan",
               write_json(os.path.join(folder, "far.json"), {"contours": [far], "captions": caption}))
    row = json.load(open(os.path.join(folder, "poster_液化.checks", "report.json")))["captions"][0]
    ok("look bent" not in text and row["moved_max"] < 1, "an edge far from the text leaves it alone, and says so")
    part = dict(part="thigh", name="thigh", width=600, toward=[1400, 1750],
                edge=[[800 + 0.8 * (y - 1400), y] for y in range(1400, 2101, 100)])
    text = run(folder, "apply", mine, "--plan-only", "--plan",
               write_json(os.path.join(folder, "part.json"), {"parts": [part], "captions": caption}))
    ok("reaches the text" in text and "not level" in text, "a part that would slant text is warned about before running")
    text = run(folder, "apply", mine, "--plan-only", "--plan",
               write_json(os.path.join(folder, "level.json"), {"parts": [dict(part, direction=[1, 0])], "captions": caption}))
    ok("not level" not in text and "[WARN]" not in text, "with a level `direction` it is not")


# ---------------------------------------------------------------- tone (engine)

def suite_tone(work, photo):
    folder = os.path.join(work, "tone")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    image = cv2.imread(photo).astype(np.float32)
    graded = image.copy()
    graded[:, :, 0] = np.clip(image[:, :, 0] * 1.07 + 3, 0, 255)   # blue up, red down: a cooler grade
    graded[:, :, 2] = np.clip(image[:, :, 2] * 0.96, 0, 255)
    reference = os.path.join(folder, "reference.png")
    cv2.imwrite(reference, graded.astype(np.uint8))
    height, width = image.shape[:2]
    boxes = []
    for fx, fy in ((0.45, 0.30), (0.50, 0.75), (0.15, 0.15), (0.80, 0.20), (0.30, 0.55)):
        x, y = int(width * fx), int(height * fy)
        boxes.append([x, y, x + 160, y + 160])
    pairs = write_json(os.path.join(folder, "pairs.json"),
                       {"pairs": [dict(name=f"patch {i}", photo=box, reference=box) for i, box in enumerate(boxes[:4])],
                        "surroundings": [dict(name="around", photo=boxes[4], reference=boxes[4])]})
    tone = os.path.join(folder, "tone.json")
    text = run(folder, "tone", mine, reference, "--pairs", pairs, "--out", tone)
    ok("[SUCCESS]" in text and os.path.isfile(os.path.join(folder, "tone.patches.jpg")), "tone writes layers and the patches")
    if not os.path.isfile(tone):
        return
    fitted = json.load(open(tone))
    worst = max(row["colour_off"] for row in fitted["measured"] if row["group"] == "subject")
    ok(worst <= 3, f"the fitted layers bring every subject patch within 3 of the reference's colour ({worst})")
    curves = [layer["params"] for layer in fitted["layers"] if layer["command"].endswith(".curves")]
    ok(len(curves) == 1 and curves[0].get("pencil") is True
       and all(len(curves[0][c]) == 256 and all(b >= a for a, b in zip(curves[0][c], curves[0][c][1:]))
               for c in ("red", "green", "blue") if c in curves[0]),
       "among them one curves layer, drawn (256 rising levels per channel)")
    ok("neutral grey" in text and "tint neutral grey" not in text, "what they do to neutral grey is shown, and is small")
    run(folder, "apply", mine, "--tone", tone)
    stem = os.path.join(folder, "portrait_液化")
    result = cv2.imread(stem + ".jpg")
    off = []
    for x0, y0, x1, y1 in boxes[:4]:
        a, b = L.to_lab(L.median_rgb(result, (x0, y0, x1, y1))), L.to_lab(L.median_rgb(cv2.imread(reference), (x0, y0, x1, y1)))
        off.append(math.hypot(a[1] - b[1], a[2] - b[2]))
    ok(max(off) <= 3.5, f"and so does the picture PhotoCraft renders with them ({max(off):.1f})")
    names = layer_names(stem + ".pcraft")
    ok(len(names) == len(fitted["layers"]) + 1 and names[-1][1] == "Smart Object"
       and all(kind == "Adjustment" and name.startswith("色调") for name, kind in names[:-1]),
       f"tone only: the photo below, the tone layers above ({[name for name, _ in names]})")
    same = write_json(os.path.join(folder, "same.json"),
                      {"pairs": [dict(name=f"patch {i}", photo=box, reference=box) for i, box in enumerate(boxes[:3])]})
    text = run(folder, "tone", mine, mine, "--pairs", same, "--out", os.path.join(folder, "none.json"), expect=1)
    ok("already matches" in text and not os.path.exists(os.path.join(folder, "none.json")),
       "a photo that already matches gets no tone file")


# ---------------------------------------------------------------- guards (engine)

def suite_guards(work, photo):
    folder = os.path.join(work, "guards")
    os.makedirs(folder)
    mine = shutil.copy(photo, os.path.join(folder, "portrait.jpg"))
    before = sha(mine)
    part = dict(part="hip", name="hip", width=900, toward=[1300, 1700], edge=[[800, 1400 + 100 * i] for i in range(6)])
    plan = write_json(os.path.join(folder, "plan.json"), {"parts": [part]})
    stem = os.path.join(folder, "portrait_液化")
    text = run(folder, "apply", mine, "--plan", plan, "--out", mine, expect=1)
    ok("never replaced" in text and sha(mine) == before, "--out naming the photo is refused; the photo is as it was")
    text = run(folder, "apply", mine, expect=1)
    ok("nothing to do" in text, "no plan and no tone: said, nothing written")
    bad = write_json(os.path.join(folder, "bad.json"), {"parts": [dict(part, part="chin")]})
    text = run(folder, "apply", mine, "--plan", bad, expect=1)
    ok("[Error]" in text and not os.path.exists(stem + ".jpg"), "a bad plan stops before anything is written")
    text = run(folder, "apply", mine, "--plan", plan, "--plan-only")
    plans = [name for name in os.listdir(stem + ".checks") if name.endswith(".plan.jpg")]
    ok(plans and not os.path.exists(stem + ".jpg"), "--plan-only draws the plan and writes no result")
    open(stem + ".jpg", "wb").write(b"someone else's file")
    text = run(folder, "apply", mine, "--plan", plan, expect=1)
    ok("not as this script last wrote it" in text and open(stem + ".jpg", "rb").read() == b"someone else's file",
       "a file already at the result's place is not replaced")
    run(folder, "apply", mine, "--plan", plan, "--force")
    run(folder, "apply", mine, "--plan", plan)
    with open(stem + ".pcraft", "ab") as handle:
        handle.write(b"edited")
    edited = sha(stem + ".pcraft")
    text = run(folder, "apply", mine, "--plan", plan, expect=1)
    ok("not as this script last wrote it" in text and sha(stem + ".pcraft") == edited,
       "a .pcraft changed since it was written is not replaced without --force")
    run(folder, "apply", mine, "--plan", plan, "--force")
    ok(sha(stem + ".pcraft") != edited and sha(mine) == before, "with --force it is; the photo still untouched")
    outside = write_json(os.path.join(folder, "outside.json"),
                         {"parts": [dict(part, edge=[[800, 1400], [800, 1500], [99999, 1600]])]})
    text = run(folder, "apply", mine, "--plan", outside, expect=1)
    ok("outside the" in text, "a point outside the picture is refused")


SUITES = {"pin": suite_pin, "plan": suite_plan, "grid": suite_grid, "liquify": suite_liquify, "captions": suite_captions,
          "tone": suite_tone, "guards": suite_guards}

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
