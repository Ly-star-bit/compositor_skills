#!/usr/bin/env python3
"""Reshape a portrait with PhotoCraft's Liquify filter and match its tone to a reference picture, headless.

    pcraft_liquify.py grid  PHOTO --out VIEW.jpg [--box X0 Y0 X1 Y1]       a view with a pixel grid, to read positions
    pcraft_liquify.py probe PHOTO --row Y --from X0 --to X1                where lightness steps: to pin down an edge
    pcraft_liquify.py apply PHOTO --plan PLAN.json [--tone TONE.json]      liquify (+ tone): PHOTO_液化.jpg / .pcraft
PHOTO is a picture (JPEG, PNG, TIFF) or a layered document (.pcraft, .psd): the liquify always goes on a stamp of
what is visible (盖印图层), a new layer on top, so the photo and every layer below stay as they were.
    pcraft_liquify.py tone  PHOTO REFERENCE --pairs PAIRS.json --out TONE.json    tone layers measured from a reference

Everything PhotoCraft does goes through its MCP server (`photocraft-cli mcp`, see photocraft_mcp.py): no window opens.
The plan names body parts and traces their edges; how far each edge moves comes from RECIPES below, as a share of the
part's own width, so the same plan logic holds at any resolution. What the engine's strokes really do, and why the
amounts are corrected against a measured field, is in references/engine.md; how to trace each part is in
references/body-parts.md.
"""
import argparse
import copy
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

SUFFIX = "_液化"
DOCUMENTS = (".pcraft", ".psd", ".psb")  # layered: looked at as PhotoCraft renders them
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
# Recipes: how far each kind of edge moves at strength 1, in percent of the part's own width
# ==========================================
# The numbers are the ones a client accepted on a 4000 x 5333 studio portrait (see references/body-parts.md); parts
# marked "start" there were not on that picture and begin from the nearest part that was.
PROFILE, AROUND, LIMB, JOINT = "profile", "around", "limb", "joint"
RECIPES = {
    # percent by position along the edge: 0 = first point, 1 = last point
    "jaw": dict(kind=PROFILE, brush=0.40,
                knots=[(0, 1.0), (0.2, 1.9), (0.37, 2.7), (0.5, 3.3), (0.66, 3.5), (0.83, 2.7), (1, 1.35)]),
    "cheek": dict(kind=PROFILE, brush=0.46, knots=[(0, 1.15), (0.33, 1.9), (0.67, 2.1), (1, 1.5)]),
    "neck": dict(kind=PROFILE, brush=0.8, knots=[(0, 2.0), (0.3, 3.5), (0.7, 3.5), (1, 2.0)]),
    "shoulder": dict(kind=PROFILE, brush=1.3, knots=[(0, 5.0), (0.5, 8.0), (1, 9.0)]),
    "hip": dict(kind=PROFILE, brush=0.38, knots=[(0, 2.6), (1, 2.6)]),
    # percent by distance from the narrowest point (the edge point nearest `toward`), in widths: minus = before it
    "waist": dict(kind=AROUND, brush=0.5,
                  knots=[(-0.45, 2.5), (-0.27, 5.8), (-0.12, 7.2), (0, 7.5), (0.12, 7.2), (0.3, 5.8), (0.6, 4.6),
                         (1.0, 3.8)]),
    # an even inset (`base`) plus `flatten` of whatever the edge bows out beyond `slack`, from its first-to-last line.
    # `tail`: the last share of the edge, next to a joint, gets its own smaller brush; `fade`: (from, to) the amount
    # runs down to `to` of itself over the last part of the edge (a wrist, an ankle)
    # `most`: where the total levels off, so tracing a little further round a joint cannot push past what was accepted
    "upper_arm": dict(kind=LIMB, brush=1.3, base=8.0, flatten=0.40, slack=10.0, most=28.0, tail=0.22),
    "forearm": dict(kind=LIMB, brush=1.0, base=8.0, flatten=0.30, slack=10.0, most=14.0, fade=(0.75, 0.4)),
    "thigh": dict(kind=LIMB, brush=0.38, base=4.5, flatten=0.35, slack=8.0, most=12.0, tail=0.2),
    "calf": dict(kind=LIMB, brush=0.9, base=5.0, flatten=0.35, slack=8.0, most=12.0, fade=(0.8, 0.5)),
    "belly": dict(kind=LIMB, brush=0.5, base=2.0, flatten=0.40, slack=4.0, most=10.0),
    "back": dict(kind=LIMB, brush=0.5, base=2.0, flatten=0.30, slack=4.0, most=8.0),
    # the same small inset all the way round the joint, times `tip` at the middle so the corner stays round
    "elbow": dict(kind=JOINT, brush=1.0, inset=8.5, tip=1.12),
    "knee": dict(kind=JOINT, brush=0.9, inset=4.0, tip=1.08),
    "wrist": dict(kind=JOINT, brush=1.0, inset=3.0, tip=1.0),
    "ankle": dict(kind=JOINT, brush=1.0, inset=3.0, tip=1.0),
}
# One point each: dabs of the Bloat or Pucker tool. size = `size` x width; `dabs` dabs at `rate` at strength 1.
POINT_RECIPES = {
    "eye": dict(tool="bloat", size=1.7, dabs=2, rate=80),   # width = the eye, corner to corner; about +7%
    "nose": dict(tool="pucker", size=1.6, dabs=2, rate=60),  # width = the nostril wings; about -4%
}
# How much THIS person needs a part done, judged from the picture before tracing (SKILL.md step 2). A recipe is what
# was accepted on one sitter who clearly needed it; the same share of a face that is slim already makes a cone of it.
NEEDS = {"clear": 1.0, "some": 0.5, "none": 0.0}
FACE = ("jaw", "cheek")
PART_LIMIT = 30.0        # no edge moves further than this share (%) of its part's width
PICTURE_LIMIT = 0.05     # ... nor further than this share of the picture's long side
BRUSH_PER_PUSH = 8.5     # a brush at least this many times the push keeps the stretch around the edge gentle


def interpolate(x, knots):
    xs, ys = zip(*knots)
    return float(np.interp(x, xs, ys))


def lengths(points):
    """Distance along the polyline to each point."""
    total = [0.0]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total.append(total[-1] + math.hypot(x1 - x0, y1 - y0))
    return total


def edge_normals(points, toward=None, direction=None):
    """A unit push direction for each point: `direction` for all, or the edge's normal on the side of `toward`."""
    if direction is not None:
        norm = math.hypot(*direction)
        if norm < 1e-6:
            raise Fail("a `direction` of [0, 0]")
        return [(direction[0] / norm, direction[1] / norm)] * len(points)
    if toward is None:
        raise Fail("an edge needs `toward` (a point inside the body part) or `direction`")
    out, previous = [], None
    for index, (x, y) in enumerate(points):
        ax, ay = points[max(index - 1, 0)]
        bx, by = points[min(index + 1, len(points) - 1)]
        tx, ty = bx - ax, by - ay
        norm = math.hypot(tx, ty)
        if norm < 1e-6:
            raise Fail(f"two edge points at the same place near [{x}, {y}]")
        nx, ny = -ty / norm, tx / norm
        side = nx * (toward[0] - x) + ny * (toward[1] - y)
        if abs(side) < 0.15 * math.hypot(toward[0] - x, toward[1] - y) and previous:
            side = nx * previous[0] + ny * previous[1]  # `toward` nearly in line with the edge here: keep the side
        if side < 0:
            nx, ny = -nx, -ny
        previous = (nx, ny)
        out.append(previous)
    return out


def as_width(value, label):
    """`width`: a number of pixels, or two points [[x, y], [x, y]] to measure between."""
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    try:
        (x0, y0), (x1, y1) = value
        width = math.hypot(x1 - x0, y1 - y0)
    except (TypeError, ValueError):
        raise Fail(f"{label}: `width` must be a number of pixels or two points [[x, y], [x, y]]") from None
    if width <= 0:
        raise Fail(f"{label}: the two `width` points are the same")
    return width


def edge_points(part, label):
    edge = part.get("edge")
    if not isinstance(edge, list) or len(edge) < 3:
        raise Fail(f"{label}: `edge` needs at least 3 points [[x, y], ...], in order along the edge")
    try:
        return [(float(x), float(y)) for x, y in edge]
    except (TypeError, ValueError):
        raise Fail(f"{label}: every `edge` point must be [x, y]") from None


def no_part(label, kind):
    return (f"{label}: no part `{kind}`. Edges: {', '.join(sorted(RECIPES))}; points: "
            f"{', '.join(sorted(POINT_RECIPES))}; anything else goes in `contours`")


def part_need(part, label):
    """The share of its recipe a part gets from `need`. Every part has to say it: full is not a default to fall into."""
    need = part.get("need")
    if need is None:
        face = (" On a face with no jaw corner standing out it makes a cone of the chin."
                if part.get("part") in FACE else "")
        warn(f"{label}: no \"need\", so this is the full amount, which was accepted on a person who plainly needed it."
             f"{face} Look at THIS person and write \"need\": \"clear\" (it plainly bulges or is wide: the full "
             "amount), \"some\" (it shows, but mildly: half) or \"none\" (fine as it is: left alone). Between two of "
             "them, take the lighter")
        return 1.0
    if need not in NEEDS:
        raise Fail(f"{label}: \"need\" is \"clear\", \"some\" or \"none\" (judge the person first, SKILL.md step 2), "
                   f"not {need!r}")
    return NEEDS[need]


def part_percent(recipe, points, toward, width, normals=None):
    """Percent of the width each edge point moves, at strength 1."""
    along = lengths(points)
    total = along[-1] or 1.0
    kind = recipe["kind"]
    if kind == PROFILE:
        return [interpolate(s / total, recipe["knots"]) for s in along]
    if kind == AROUND:
        nearest = min(range(len(points)), key=lambda i: math.hypot(points[i][0] - toward[0], points[i][1] - toward[1]))
        return [interpolate((s - along[nearest]) / width, recipe["knots"]) for s in along]
    if kind == JOINT:
        return [recipe["inset"] * (1 + (recipe["tip"] - 1) * math.sin(math.pi * s / total) ** 2) for s in along]
    (ax, ay), (bx, by) = points[0], points[-1]
    chord = math.hypot(bx - ax, by - ay) or 1.0
    cx, cy = (bx - ax) / chord, (by - ay) / chord
    inward = normals or edge_normals(points, toward)
    out = []
    for (x, y), s, (ux, uy) in zip(points, along, inward):
        along_chord = (x - ax) * cx + (y - ay) * cy
        off_x, off_y = x - (ax + cx * along_chord), y - (ay + cy * along_chord)  # from the first-to-last line
        outward = -(off_x * ux + off_y * uy)  # positive: the point sits out from that line, away from the inside
        bow = math.copysign(math.hypot(off_x, off_y), outward) / width * 100  # the bulge, % of the width
        percent = recipe["base"] + recipe["flatten"] * max(0.0, bow - recipe["slack"])
        knee = 0.75 * recipe["most"]
        if percent > knee:  # bend over toward `most` instead of running on
            percent = knee + (recipe["most"] - knee) * math.tanh((percent - knee) / (recipe["most"] - knee))
        if "fade" in recipe and s / total > recipe["fade"][0]:
            share = (s / total - recipe["fade"][0]) / (1 - recipe["fade"][0])
            percent *= 1 + (recipe["fade"][1] - 1) * share
        out.append(percent)
    return out


def even_size(value):
    return max(20, int(round(value / 2.0)) * 2)


def part_contours(part, index, strength, long_side):
    """A traced body part -> one or two contours [{name, size, points: [(x, y, ux, uy, px)]}]."""
    kind = part.get("part")
    label = part.get("name") or f"{kind} #{index + 1}"
    if kind not in RECIPES:
        raise Fail(no_part(label, kind))
    recipe = RECIPES[kind]
    share = part_need(part, label)
    if share == 0:
        return []
    points = edge_points(part, label)
    width = as_width(part.get("width"), label)
    toward = part.get("toward")
    if toward is None and (recipe["kind"] in (AROUND, LIMB) or part.get("direction") is None):
        raise Fail(f"{label}: `{kind}` needs `toward`: [x, y], a point inside the body part (the pushes go that way)")
    try:
        normals = edge_normals(points, toward, part.get("direction"))
    except Fail as error:
        raise Fail(f"{label}: {error}") from None
    factor = float(part.get("strength", 1.0)) * strength
    if not 0 < factor <= 1.8:
        raise Fail(f"{label}: strength {factor:g} (the part's times --strength) must be above 0 and at most 1.8")
    factor *= share
    inward = edge_normals(points, toward) if toward is not None else None
    percent = [p * factor for p in part_percent(recipe, points, toward, width, inward)]
    if inward is not None:  # two readings of one thing: the centre of a part W wide is W/2 in from its edge
        depth = sorted((toward[0] - x) * ux + (toward[1] - y) * uy for (x, y), (ux, uy) in zip(points, inward))
        depth = depth[len(depth) // 2]
        if depth > 0 and not 0.45 <= width / (2 * depth) <= 2.5:
            say(f"[NOTE] {label}: `width` is {width:.0f} but `toward` lies {depth:.0f} px in from the edge; the centre of "
                f"a part {width:.0f} px wide is about {width / 2:.0f} px in. One of the two is off: check `width` "
                "first (every push is a share of it)")
    limit = min(PART_LIMIT, 100 * PICTURE_LIMIT * long_side / width)
    if max(percent) > limit:
        warn(f"{label}: an edge would move {max(percent):.0f}% of the part's width; held at {limit:.0f}%. "
             "Check `width` and the first and last edge points")
        percent = [min(p, limit) for p in percent]
    amounts = [p / 100 * width for p in percent]
    gaps = [b - a for a, b in zip(lengths(points), lengths(points)[1:])]
    size = part.get("brush") or max(recipe["brush"] * width, BRUSH_PER_PUSH * max(amounts))
    size = even_size(float(size))
    if size < 5 * max(amounts):
        warn(f"{label}: a {size} px brush is small for a {max(amounts):.0f} px push: what lies right beside this edge "
             f"gets stretched hard. Fine where both sides are plain (one flat colour); otherwise give a brush of "
             f"{even_size(6 * max(amounts))} or more, or \"strength\": {max(0.3, size / 6 / max(amounts) * float(part.get('strength', 1.0))):.1f}")
    if max(gaps) > size * 0.75:
        warn(f"{label}: edge points up to {max(gaps):.0f} px apart with a {size} px brush: add points in between, "
             "so the push follows the edge")
    # An edge cut short on purpose (text, a hand or a prop is in the way) runs out gently instead of ending in a step.
    along = lengths(points)
    reach = max(1.0, min(0.65 * size, along[-1] / 3))
    for flag, distance in (("ease_start", lambda s: s), ("ease_end", lambda s: along[-1] - s)):
        if part.get(flag):
            for i, s_ in enumerate(along):
                t = min(1.0, distance(s_) / reach)
                amounts[i] *= 0.2 + 0.8 * t * t * (3 - 2 * t)
    rows = [(x, y, ux, uy, px) for (x, y), (ux, uy), px in zip(points, normals, amounts)]
    tail = recipe.get("tail")
    if tail and not part.get("brush") and not part.get("ease_end") and len(rows) >= 5:
        first = next((i for i, s in enumerate(along) if s / along[-1] >= 1 - tail), len(rows))
        first = max(first, 2)
        if first < len(rows) - 1 and max(amounts[first:]) < 0.75 * max(amounts):
            small = even_size(max(recipe["brush"] * width, BRUSH_PER_PUSH * max(amounts[first:])))
            if small < 0.8 * size:
                return [dict(name=label, part=kind, width=width, size=size, points=rows[:first], toward=toward,
                             need=part.get("need")),
                        dict(name=f"{label} (近关节)", part=kind, width=width, size=small, points=rows[first:],
                             need=part.get("need"))]
    return [dict(name=label, part=kind, width=width, size=size, points=rows, toward=toward, need=part.get("need"))]


def raw_contour(entry, index):
    label = entry.get("name") or f"contour #{index + 1}"
    points = entry.get("points")
    if not isinstance(points, list) or len(points) < 2 or not entry.get("size"):
        raise Fail(f"{label}: a contour needs `size` and `points` [[x, y, px] or [x, y, px, ux, uy], ...]")
    plain = [(float(p[0]), float(p[1])) for p in points]
    normals = None
    rows = []
    for position, point in enumerate(points):
        if len(point) >= 5:
            norm = math.hypot(point[3], point[4]) or 1.0
            direction = (point[3] / norm, point[4] / norm)
        else:
            if normals is None:
                normals = edge_normals(plain, entry.get("toward"), entry.get("direction"))
            direction = normals[position]
        rows.append((plain[position][0], plain[position][1], direction[0], direction[1], float(point[2])))
    return dict(name=label, part="contour", width=None, size=even_size(float(entry["size"])), points=rows)


def along_borders(contour, width, height):
    """Near a border of the picture a push keeps only its part along that border: nothing lies beyond the border
    to be drawn in, so pushing toward or away from it smears the last rows."""
    reach = contour["size"] / 2
    rows = []
    for x, y, ux, uy, px in contour["points"]:
        keep_x = min(1.0, max(0.0, min(x, width - 1 - x) / reach))
        keep_y = min(1.0, max(0.0, min(y, height - 1 - y) / reach))
        vx, vy = ux * keep_x, uy * keep_y
        norm = math.hypot(vx, vy)
        rows.append((x, y, vx / norm, vy / norm, px * norm) if norm > 0.05 else (x, y, ux, uy, 0.0))
    contour["points"] = rows


def read_plan(plan, strength, width, height):
    """The plan -> (contours, nudges, dabs): everything in picture pixels."""
    long_side = max(width, height)
    contours, nudges, dabs = [], [], []
    for index, part in enumerate(plan.get("parts") or []):
        kind = part.get("part")
        label = part.get("name") or f"{kind} #{index + 1}"
        if kind not in RECIPES and kind not in POINT_RECIPES:
            raise Fail(no_part(label, kind))
        if part.get("need") == "none":  # judged, written down, and not done: the plan says so and so does the run
            say(f"[NOTE] {label}: need \"none\", left as it is" + (f" ({part['why']})" if part.get("why") else ""))
            continue
        if kind in POINT_RECIPES:
            recipe = POINT_RECIPES[kind]
            if not part.get("at"):
                raise Fail(f"{label}: `{kind}` needs `at`: [x, y], its centre")
            span = as_width(part.get("width"), label)
            wanted = recipe["dabs"] * float(part.get("strength", 1.0)) * strength * part_need(part, label)
            count = max(1, math.ceil(wanted - 1e-6))
            dabs.append(dict(name=label, tool=recipe["tool"], size=even_size(recipe["size"] * span),
                             at=[float(part["at"][0]), float(part["at"][1])], count=count,
                             rate=max(1, min(100, round(recipe["rate"] * wanted / count)))))
        else:
            contours += part_contours(part, index, strength, long_side)
    for index, entry in enumerate(plan.get("contours") or []):
        contour = raw_contour(entry, index)
        contour["points"] = [row[:4] + (row[4] * strength,) for row in contour["points"]]
        contours.append(contour)
    for index, entry in enumerate(plan.get("nudges") or []):
        label = entry.get("name") or f"nudge #{index + 1}"
        try:
            at = (float(entry["at"][0]), float(entry["at"][1]))
            if "toward" in entry:
                direction = (entry["toward"][0] - at[0], entry["toward"][1] - at[1])
            else:
                direction = (float(entry["direction"][0]), float(entry["direction"][1]))
            norm = math.hypot(*direction)
            nudges.append(dict(name=label, size=even_size(float(entry["size"])), at=at,
                               direction=(direction[0] / norm, direction[1] / norm), px=float(entry["px"]) * strength))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            raise Fail(f"{label}: a nudge needs `size`, `at` [x, y], `px` and `direction` [ux, uy] (or `toward` "
                       "[x, y])") from None
    for index, entry in enumerate(plan.get("dabs") or []):
        label = entry.get("name") or f"dab #{index + 1}"
        if entry.get("tool") not in ("bloat", "pucker") or not entry.get("at") or not entry.get("size"):
            raise Fail(f"{label}: a dab needs `tool` (bloat or pucker), `size` and `at` [x, y]")
        dabs.append(dict(name=label, tool=entry["tool"], size=even_size(float(entry["size"])),
                         at=[float(entry["at"][0]), float(entry["at"][1])], count=int(entry.get("count", 1)),
                         rate=max(1, min(100, round(float(entry.get("rate", 80)) * strength)))))
    for contour in contours:
        along_borders(contour, width, height)
        for x, y, ux, uy, px in contour["points"]:
            if px > PICTURE_LIMIT * long_side:
                raise Fail(f"{contour['name']}: a push of {px:.0f} px is more than {100 * PICTURE_LIMIT:.0f}% of the "
                           "picture's long side. That is not a retouch; check the numbers")
    return contours, nudges, dabs


# ==========================================
# Strokes (see references/engine.md for the measurements behind each rule)
# ==========================================

def falloff(t):
    """A forwardWarp brush at density 50: how much of the drag reaches a point at t radii from its centre."""
    return (1 - t * t) ** 1.5 if t < 1 else 0.0


def drag(size, x, y, ux, uy, amount):
    """One forwardWarp drag that moves what is under the brush by `amount` px along (ux, uy). The engine steps a drag
    every 2.5% of the brush size and drops the remainder, so the drag is a whole number of steps (at least two) and
    the pressure carries the amount."""
    spacing = size * 0.025
    length = max(2, math.ceil(amount / spacing - 1e-6)) * spacing
    start = (amount - length) / 2
    return {"tool": "forwardWarp", "size": size, "density": 50, "pressure": round(100 * amount / length, 2),
            "points": [[round(x + ux * start, 2), round(y + uy * start, 2)],
                       [round(x + ux * (start + length + 0.01), 2), round(y + uy * (start + length + 0.01), 2)]]}


def chain(size, points, spacing=0.25):
    """Drags a quarter radius apart along the polyline; their overlap adds up to the amount each point asks for."""
    step = size / 2 * spacing
    gain = sum(falloff(abs(k) * spacing) for k in range(-8, 9))
    strokes = []
    for (x0, y0, ux0, uy0, p0), (x1, y1, ux1, uy1, p1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        count = max(1, round(length / step))
        for index in range(count):
            t = index / count
            ux, uy = ux0 + (ux1 - ux0) * t, uy0 + (uy1 - uy0) * t
            norm = math.hypot(ux, uy) or 1.0
            amount = (p0 + (p1 - p0) * t) / gain * (length / count) / step
            if amount > 0.05:
                strokes.append(drag(size, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, ux / norm, uy / norm, amount))
    x, y, ux, uy, p = points[-1]
    if p / gain > 0.05:
        strokes.append(drag(size, x, y, ux, uy, p / gain))
    return strokes


def build_strokes(contours, nudges, dabs, scale=1.0):
    """Every stroke of the liquify, in one list (one filter call, so the picture is resampled once)."""
    strokes = []
    for contour in contours:
        points = [(x * scale, y * scale, ux, uy, px * scale) for x, y, ux, uy, px in contour["points"]]
        strokes += chain(max(4, contour["size"] * scale), points)
    for nudge in nudges:
        if nudge["px"] > 0.05:
            strokes.append(drag(max(4, nudge["size"] * scale), nudge["at"][0] * scale, nudge["at"][1] * scale,
                                nudge["direction"][0], nudge["direction"][1], nudge["px"] * scale))
    for dab in dabs:
        strokes.append({"tool": dab["tool"], "size": max(4, dab["size"] * scale), "density": 50, "pressure": 100,
                        "rate": dab["rate"],
                        "points": [[dab["at"][0] * scale, dab["at"][1] * scale]] * max(1, dab["count"])})
    return strokes


# ==========================================
# Pictures
# ==========================================

def need_libraries():
    if cv2 is None:
        raise Fail(f"{_IMPORT_ERROR}: python3 -m pip install \"opencv-python-headless<5\" numpy")


def read_picture(path):
    need_libraries()
    try:
        data = np.fromfile(path, np.uint8)
    except OSError as error:
        raise Fail(f"cannot read {path}: {error}") from error
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise Fail(f"{path} is not a picture this can read (JPEG, PNG or TIFF; convert HEIC or RAW first)")
    return image


def read_raw(path, flags):
    """cv2.imread for any path (OpenCV's own takes only names in the system's encoding on Windows)."""
    try:
        return cv2.imdecode(np.fromfile(path, np.uint8), flags)
    except OSError:
        return None


def write_raw(path, image):
    ok, data = cv2.imencode(os.path.splitext(path)[1], image)
    if not ok:
        raise Fail(f"could not write {path}")
    data.tofile(path)


def write_picture(path, image, quality=88):
    extension = os.path.splitext(path)[1].lower() or ".jpg"
    ok, data = cv2.imencode(extension, image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise Fail(f"could not write {path}")
    data.tofile(path)


def label(image, text, at=(8, 24), scale=0.7):
    text = "".join(c if 31 < ord(c) < 127 else "?" for c in text)  # OpenCV's built-in fonts are ASCII only
    cv2.putText(image, text, at, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, text, at, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 255, 255), 2, cv2.LINE_AA)


def fit(image, side):
    scale = side / max(image.shape[:2])
    method = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=method), scale


def clamp_box(box, width, height, name="box"):
    try:
        x0, y0, x1, y1 = (int(round(float(v))) for v in box)
    except (TypeError, ValueError):
        raise Fail(f"{name}: a box is [x0, y0, x1, y1]") from None
    x0, x1 = sorted((max(0, min(width, x0)), max(0, min(width, x1))))
    y0, y1 = sorted((max(0, min(height, y0)), max(0, min(height, y1))))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise Fail(f"{name}: [{', '.join(str(v) for v in box)}] is empty or outside the {width} x {height} picture")
    return x0, y0, x1, y1


def lift(tiles, force=False):
    """Dark crops shown lighter (the same curve for every tile), for looking only. Returns the tiles and whether."""
    mean = float(cv2.cvtColor(tiles[0], cv2.COLOR_BGR2GRAY).mean())
    if mean >= 70 and not force:
        return tiles, False
    gamma = min(0.85, max(0.4, math.log(105 / 255) / math.log(max(mean, 4) / 255)))
    table = (np.linspace(0, 1, 256) ** gamma * 255).astype(np.uint8)
    return [cv2.LUT(tile, table) for tile in tiles], True


def grid_view(image, box, side, step, brighten=False):
    """The crop `box`, at most `side` px long, with a grid labelled in whole-picture pixels."""
    height, width = image.shape[:2]
    x0, y0, x1, y1 = clamp_box(box or (0, 0, width, height), width, height)
    crop, scale = fit(image[y0:y1, x0:x1], side)
    if brighten:
        crop = lift([crop], force=True)[0][0]
    if not step:
        span = max(x1 - x0, y1 - y0)
        step = next(s for s in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000) if span / s <= 16)
    for x in range((x0 // step + 1) * step, x1, step):
        px = int(round((x - x0) * scale))
        cv2.line(crop, (px, 0), (px, crop.shape[0]), (0, 255, 255), 1)
        label(crop, f"x{x}", (px + 3, 15), 0.45)
    for y in range((y0 // step + 1) * step, y1, step):
        py = int(round((y - y0) * scale))
        cv2.line(crop, (0, py), (crop.shape[1], py), (0, 255, 255), 1)
        label(crop, f"y{y}", (3, py - 4), 0.45)
    return crop, scale, step


def draw_plan(image, contours, nudges, dabs, box, scale):
    """Edge points, the edge, and each point's push at its true length, on a crop already scaled by `scale`."""
    x0, y0 = box[0], box[1]

    def place(x, y):
        return int(round((x - x0) * scale)), int(round((y - y0) * scale))
    for contour in contours:
        points = contour["points"]
        for a, b in zip(points, points[1:]):
            cv2.line(image, place(a[0], a[1]), place(b[0], b[1]), (255, 255, 0), 1, cv2.LINE_AA)
        for x, y, ux, uy, px in points:
            cv2.arrowedLine(image, place(x, y), place(x + ux * px, y + uy * px), (0, 255, 0), 2, cv2.LINE_AA,
                            tipLength=0.3)
            cv2.circle(image, place(x, y), 4, (0, 0, 255), -1, cv2.LINE_AA)
        if contour.get("toward"):
            tx, ty = place(*contour["toward"])
            cv2.drawMarker(image, (tx, ty), (0, 255, 255), cv2.MARKER_TILTED_CROSS, 18, 2, cv2.LINE_AA)
            label(image, "toward", (tx + 12, ty + 5), 0.45)
        if not contour["name"].endswith(")") or "(" not in contour["name"]:  # not the second run of one part
            first = place(points[0][0], points[0][1])
            cv2.circle(image, first, 11, (255, 128, 0), 2, cv2.LINE_AA)  # blue ring: where the edge starts
            tag = contour["name"] if all(31 < ord(c) < 127 for c in contour["name"]) else str(contour.get("part"))
            label(image, f"1 {tag}", (first[0] + 14, first[1] + 5), 0.5)
    for nudge in nudges:
        x, y = nudge["at"]
        cv2.arrowedLine(image, place(x, y), place(x + nudge["direction"][0] * nudge["px"],
                                                  y + nudge["direction"][1] * nudge["px"]), (0, 200, 255), 2,
                        cv2.LINE_AA, tipLength=0.3)
        cv2.circle(image, place(x, y), int(nudge["size"] / 2 * scale), (0, 200, 255), 1, cv2.LINE_AA)
    for dab in dabs:
        cv2.circle(image, place(*dab["at"]), max(2, int(dab["size"] / 2 * scale)), (255, 0, 255), 1, cv2.LINE_AA)


def auto_views(contours, nudges, dabs, width, height, limit=1700):
    """A close view for each part the plan touches. Neighbours share a view while it stays under `limit` px a side,
    so every view is shown large enough to judge an edge."""
    boxes = {}
    for contour in contours:
        reach = contour["size"] * 0.35
        xs = [p[0] for p in contour["points"]]
        ys = [p[1] for p in contour["points"]]
        key = contour["name"].split(" (")[0]
        box = [min(xs) - reach, min(ys) - reach, max(xs) + reach, max(ys) + reach]
        old = boxes.get(key)
        boxes[key] = box if old is None else [min(old[0], box[0]), min(old[1], box[1]), max(old[2], box[2]),
                                               max(old[3], box[3])]
    for item in nudges + dabs:
        reach = item["size"] * 0.7
        boxes[item["name"]] = [item["at"][0] - reach, item["at"][1] - reach, item["at"][0] + reach,
                               item["at"][1] + reach]
    groups = [[name, box] for name, box in boxes.items()]
    while True:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i][1], groups[j][1]
                union = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                side = max(union[2] - union[0], union[3] - union[1])
                touching = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                if touching and side <= limit and (best is None or side < best[0]):
                    best = (side, i, j, union)
        if best is None:
            break
        _, i, j, union = best
        groups[i][1] = union
        del groups[j]
    views = []
    for number, (name, box) in enumerate(sorted(groups, key=lambda g: (g[1][1], g[1][0])), 1):
        slug = "".join(c if c.isalnum() and ord(c) < 128 else "-" for c in name.lower()).strip("-") or "view"
        views.append(dict(name=f"{number:02d}-{slug}"[:40], box=clamp_box(box, width, height)))
    return views


def face_boxes(contours, dabs, width, height):
    """A square round each face the plan touched: for a clean before | after, to judge the look and not the edges."""
    boxes = []
    for contour in contours:
        if contour.get("part") in FACE and contour.get("toward") is not None:
            (x, y), half = contour["toward"], 1.1 * contour["width"]
            boxes.append([x - half, y - half, x + half, y + half])
    for dab in dabs:
        (x, y), half = dab["at"], 2.4 * dab["size"]
        boxes.append([x - half, y - half, x + half, y + half])
    merged = True
    while merged:  # the parts of one face overlap: one box a face
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return [clamp_box(box, width, height) for box in sorted(boxes)]


# ==========================================
# The engine: the measured field, the liquify, the layers
# ==========================================

class Engine:
    """A PhotoCraft MCP session in a scratch folder, the only folder the engine may touch."""

    def __init__(self):
        self.folder = os.path.realpath(tempfile.mkdtemp(prefix="pcraft-liquify-"))
        self.session = Mcp(find_cli(say, warn), self.folder)

    def path(self, name):
        return os.path.join(self.folder, name)

    def close(self):
        self.session.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def field(self, strokes, width, height):
        """(dx, dy) per pixel: how far what is now there has moved. Measured, not predicted: the strokes are run on
        a 16-bit picture whose pixels hold their own coordinates."""
        source = self.path(f"coordinates-{width}x{height}.png")
        if not os.path.exists(source):
            xs = np.broadcast_to(np.round(np.arange(width) * 65535.0 / max(1, width - 1)), (height, width))
            ys = np.broadcast_to(np.round(np.arange(height) * 65535.0 / max(1, height - 1))[:, None], (height, width))
            write_raw(source, np.stack([np.zeros_like(xs), ys, xs], axis=-1).astype(np.uint16))
        out = self.path("field.png")
        self.session.call("doc_open", {"path": source})
        try:
            if strokes:
                self.session.command("filter.liquify", {"strokes": strokes})
            self.session.call("doc_save", {"path": out})
        finally:
            self.session.call("doc_close")
        image = read_raw(out, cv2.IMREAD_UNCHANGED)
        os.remove(out)
        if image is None or image.dtype != np.uint16:
            raise Fail("the engine did not hand back a 16-bit field picture")
        image = image.astype(np.float32)
        xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
        return (xs - image[:, :, 2] * max(1, width - 1) / 65535.0, ys - image[:, :, 1] * max(1, height - 1) / 65535.0)


def landed(dx, dy, x, y, ux, uy, px, scale=1.0):
    """How far the content that started at (x, y) really moved: read the field where it should have landed."""
    height, width = dx.shape
    lx = int(round(min(width - 1, max(0, (x + ux * px) * scale))))
    ly = int(round(min(height - 1, max(0, (y + uy * px) * scale))))
    return math.hypot(dx[ly, lx], dy[ly, lx]) / scale


def shared_points(contours):
    """Points inside another contour's brush: both pushes land there, so neither's own target can hold."""
    shared = {}
    for a, contour in enumerate(contours):
        for index, (x, y, ux, uy, px) in enumerate(contour["points"]):
            for b, other in enumerate(contours):
                if a != b and other["name"].split(" (")[0] != contour["name"].split(" (")[0] and any(
                        math.hypot(x - ox, y - oy) < other["size"] / 2 and opx > 0.3 * max(px, 1)
                        for ox, oy, _, _, opx in other["points"]):
                    shared[(a, index)] = other["name"]
                    break
    return shared


def solve(engine, design, nudges, dabs, width, height, rounds):
    """Correct each control point's amount against the measured field until the edges land where the plan says.
    Chains with different brushes add up where they meet, so the first guess is never right there. Corrections are
    damped (neighbours answer each other's corrections, and undamped they swing back and forth) and the best round
    is the one kept."""
    scale = min(1.0, 2000.0 / max(width, height))  # corrections on a small field, the last check at full size
    small = (max(2, int(round(width * scale))), max(2, int(round(height * scale))))
    scale = small[0] / width
    shared = shared_points(design)

    def misses(dx, dy, at_scale):
        return [((a, i), landed(dx, dy, x, y, ux, uy, want, at_scale) - want, want)
                for a, target in enumerate(design) for i, (x, y, ux, uy, want) in enumerate(target["points"])
                if want >= 2]

    def score(rows):  # where two parts overlap neither target can hold exactly: those points count for less
        return sum(abs(miss) * (0.3 if key in shared else 1.0) for key, miss, _ in rows) / max(1, len(rows))

    def corrected(state, rows):
        state = copy.deepcopy(state)
        for (a, i), miss, want in rows:
            ratio = min(1.5, max(0.5, (want / max(want + miss, 1e-3)) ** 0.7))
            row = state[a]["points"][i]
            state[a]["points"][i] = row[:4] + (row[4] * ratio,)
        return state

    working = copy.deepcopy(design)
    if rounds and any(point[4] >= 2 for contour in design for point in contour["points"]):
        best, state = None, working
        for _ in range(rounds + 1):
            dx, dy = engine.field(build_strokes(state, nudges, dabs, scale), small[0], small[1])
            rows = misses(dx, dy, scale)
            if best is None or score(rows) < best[0]:
                best = (score(rows), state)
            state = corrected(state, rows)
        working = best[1]
    strokes = build_strokes(working, nudges, dabs)
    dx, dy = engine.field(strokes, width, height)
    rows = misses(dx, dy, 1.0)
    if rounds and max([abs(miss) for key, miss, want in rows if key not in shared and want >= 5] or [0.0]) > 4:
        again = build_strokes(corrected(working, rows), nudges, dabs)
        dx2, dy2 = engine.field(again, width, height)
        if score(misses(dx2, dy2, 1.0)) < score(rows):
            strokes, dx, dy = again, dx2, dy2
    return strokes, dx, dy, shared


def caption_check(photo, dx, dy, captions):
    """How far the glyphs of text baked into the picture moved, and how much they lean. Glyphs are thin strokes
    lighter or darker than what is round them (top-hat and black-hat)."""
    gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
    rows = []
    for index, caption in enumerate(captions):
        name = caption.get("name") or f"caption #{index + 1}"
        x0, y0, x1, y1 = clamp_box(caption.get("box"), width, height, name)
        crop = gray[y0:y1, x0:x1]
        glyphs = np.maximum(cv2.morphologyEx(crop, cv2.MORPH_TOPHAT, kernel),
                            cv2.morphologyEx(crop, cv2.MORPH_BLACKHAT, kernel)) > 35
        count = int(glyphs.sum())
        if count < 20:
            rows.append(dict(name=name, box=[x0, y0, x1, y1], glyphs=count))
            continue
        fx, fy = dx[y0:y1, x0:x1], dy[y0:y1, x0:x1]
        lean = np.degrees(np.arctan(np.abs(np.gradient(fx, axis=0))))[glyphs]
        squeeze = (100 * np.abs(np.gradient(fx, axis=1)))[glyphs]
        moved = np.hypot(fx, fy)[glyphs]
        rows.append(dict(name=name, box=[x0, y0, x1, y1], glyphs=count, moved_max=round(float(moved.max()), 1),
                         moved_share=round(float((moved > 1).mean() * 100), 1),
                         up_down_max=round(float(np.abs(fy)[glyphs].max()), 1),
                         lean_max=round(float(lean.max()), 1), lean_share=round(float((lean > 3).mean() * 100), 1),
                         squeeze_max=round(float(squeeze.max()), 1)))
    return rows


def heat_picture(photo, dx, dy, marks=()):
    small, scale = fit(photo, 1500)
    magnitude = np.hypot(dx, dy)
    top = max(20.0, float(magnitude.max()))
    heat = cv2.resize(np.clip(magnitude / top * 255, 0, 255).astype(np.uint8), (small.shape[1], small.shape[0]))
    alpha = np.clip(heat / 110.0, 0, 0.6)[:, :, None]
    blend = (small * (1 - alpha) + cv2.applyColorMap(heat, cv2.COLORMAP_TURBO) * alpha).astype(np.uint8)
    step = max(40, int(round(max(photo.shape[:2]) / 45)))
    for y in range(step // 2, photo.shape[0], step):
        for x in range(step // 2, photo.shape[1], step):
            vx, vy = float(dx[y, x]), float(dy[y, x])
            if math.hypot(vx, vy) > 3:
                cv2.arrowedLine(blend, (int((x - vx * 2) * scale), int((y - vy * 2) * scale)),
                                (int(x * scale), int(y * scale)), (255, 255, 255), 1, cv2.LINE_AA, tipLength=0.3)
    for x, y, text in marks:
        cv2.circle(blend, (int(x * scale), int(y * scale)), 26, (255, 255, 255), 2, cv2.LINE_AA)
        label(blend, text, (int(x * scale) + 30, int(y * scale) + 5), 0.5)
    label(blend, f"red = {top:.0f} px; arrows at 2x", (8, 24))
    return blend


def side_by_side(tiles):
    height = max(tile.shape[0] for tile in tiles)
    padded = []
    for tile in tiles:
        if tile.shape[0] < height:
            tile = np.vstack([tile, np.zeros((height - tile.shape[0], tile.shape[1], 3), np.uint8)])
        padded += [tile, np.full((height, 6, 3), 255, np.uint8)]
    return np.hstack(padded[:-1])


def stamp(path):
    status = os.stat(path)
    return {"size": status.st_size, "mtime": round(status.st_mtime, 3)}


def staged_name(path):
    """The picture's own name when the engine's automation takes it, else a plain one."""
    name = os.path.basename(path)
    stem = name.split(".")[0].rstrip().upper()
    if (":" in name or "\\" in name or name.endswith((".", " ")) or name.startswith(".")
            or stem in {"CON", "PRN", "AUX", "NUL"}):
        return "photo" + os.path.splitext(name)[1]
    return name


def is_document(path):
    return os.path.splitext(path)[1].lower() in DOCUMENTS


def read_source(path):
    """The picture to look at and measure: the file itself, or a layered document as PhotoCraft renders it (what is
    visible, flattened; the document is copied first and never written)."""
    if not is_document(path):
        return read_picture(path)
    need_libraries()
    if os.path.isdir(path):
        raise Fail(f"{path} is a folder bundle (PhotoCraft's recovery format): open it in PhotoCraft and save it as a "
                   "file first")
    if not os.path.isfile(path):
        raise Fail(f"cannot read {path}")
    engine = Engine()
    try:
        staged, out = engine.path(staged_name(path)), engine.path("render.png")
        shutil.copyfile(path, staged)
        engine.session.call("doc_open", {"path": staged})
        try:
            engine.session.call("doc_save", {"path": out})
        finally:
            engine.session.call("doc_close")
        image = read_raw(out, cv2.IMREAD_COLOR)
    finally:
        engine.close()
    if image is None:
        raise Fail(f"PhotoCraft could not render {path}")
    return image


def tone_layers(tone):
    """A tone file -> [(command, name, opacity, params)]."""
    layers = tone.get("layers") if isinstance(tone, dict) else None
    if not isinstance(layers, list):
        raise Fail("a tone file is {\"layers\": [{\"command\": \"layer.newAdjustmentLayer.curves\", \"name\": ..., "
                   "\"params\": {...}}, ...]}")
    out = []
    for index, layer in enumerate(layers):
        command = layer.get("command") or ""
        if not command.startswith("layer.newAdjustmentLayer.") or not isinstance(layer.get("params"), dict):
            raise Fail(f"tone layer #{index + 1}: `command` must be a layer.newAdjustmentLayer.* command with `params`")
        opacity = float(layer.get("opacity", 1.0))
        if not 0 < opacity <= 1:
            raise Fail(f"tone layer #{index + 1}: `opacity` is between 0 and 1")
        out.append((command, layer.get("name") or f"色调 {index + 1}", opacity, layer["params"]))
    return out


def new_layer(session, command, params):
    result = session.command(command, params)
    layer = result.get("layer") if isinstance(result, dict) else None
    if layer is None:
        layer = session.call("doc_inspect")["activeLayer"]
    return layer


def build_document(engine, staged, strokes, layers, jpg, pcraft, quality, layered):
    """Whatever the file holds stays below, untouched. On top: a stamp of what is visible (盖印图层) as a Smart
    Object with the liquify as its smart filter, then the tone as adjustment layers."""
    session = engine.session
    session.call("doc_open", {"path": staged})
    try:
        if not layered:
            session.command("layer.setProps", {"layer": session.call("doc_inspect")["activeLayer"], "name": "原图"})
        session.command("layer.selectTop")
        if strokes:
            stamp = session.command("layer.stampVisible")["layer"]
            converted = session.command("layer.smartObjects.convertToSmartObject", {"layer": stamp})
            stamp = (converted.get("layer") if isinstance(converted, dict) else None) or \
                session.call("doc_inspect")["activeLayer"]
            session.command("filter.liquify", {"strokes": strokes, "layer": stamp})
            session.command("layer.setProps", {"layer": stamp, "name": "液化 · 盖印（智能滤镜）"})
        for command, name, opacity, params in layers:
            layer = new_layer(session, command, params)
            session.command("layer.setProps", {"layer": layer, "name": name, "opacity": round(opacity, 4)})
        session.call("doc_save", {"path": jpg, "quality": quality})
        if pcraft:
            session.call("doc_save", {"path": pcraft})
    finally:
        session.call("doc_close")


def place(source, destination):
    """Move a finished file into place in one step."""
    part = destination + ".part"
    shutil.copyfile(source, part)
    os.replace(part, destination)


# ==========================================
# apply
# ==========================================

def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as error:
        raise Fail(f"cannot read the {what} {path}: {error}") from error
    except ValueError as error:
        raise Fail(f"the {what} {path} is not valid JSON: {error}") from error


def command_apply(args):
    need_libraries()
    started = time.time()
    photo_path = os.path.abspath(args.photo)
    layered = is_document(photo_path)
    photo = read_source(photo_path)
    height, width = photo.shape[:2]
    stem, _ = os.path.splitext(photo_path)
    out = os.path.abspath(args.out) if args.out else stem + SUFFIX + ".jpg"
    if os.path.splitext(out)[1].lower() not in (".jpg", ".jpeg"):
        raise Fail("--out names a .jpg file")
    out_stem = os.path.splitext(out)[0]
    pcraft = None if args.no_pcraft else out_stem + ".pcraft"
    sidecar, checks = out_stem + ".plan.json", out_stem + ".checks"
    for path in (out, pcraft):
        if path and os.path.exists(path) and os.path.samefile(path, photo_path):
            raise Fail(f"{path} is the {'document' if layered else 'photo'} itself: the original is never replaced. "
                       "Give --out another name")
    if not os.path.isdir(os.path.dirname(out)):
        raise Fail(f"no folder {os.path.dirname(out)}")
    if not args.plan and not args.tone:
        raise Fail("nothing to do: give --plan, --tone or both")
    if args.reference and os.path.exists(args.reference) and os.path.samefile(args.reference, photo_path):
        say("[NOTE] --reference is the photo itself: left out of the overview. It is for another picture to set "
            "beside the result, and may be left out")
        args.reference = None

    plan = {}
    if args.plan:
        plan = load_json(args.plan, "plan")
        if isinstance(plan, dict) and isinstance(plan.get("plan"), dict):
            plan = plan["plan"]  # the .plan.json this script writes beside its output
        if not isinstance(plan, dict):
            raise Fail("a plan is a JSON object: {\"parts\": [...], \"captions\": [...]}")
    tone = load_json(args.tone, "tone file") if args.tone else None
    layers = tone_layers(tone) if tone else []
    if not 0 < args.strength <= 1.8:
        raise Fail("--strength is above 0 and at most 1.8 (1 = the plan as written)")
    contours, nudges, dabs = read_plan(plan, args.strength, width, height)
    if args.plan and not (contours or nudges or dabs) and not layers:
        # Judging that nothing needs doing is a result, not a failure: no reason to invent a part to get a file out.
        say("[SUCCESS] nothing to liquify: the plan leaves every part as it is, and there is no --tone. No file "
            "written or changed. Tell the user which parts you judged and why they need nothing")
        return
    for contour in contours:
        for x, y, ux, uy, px in contour["points"]:
            if not (0 <= x < width and 0 <= y < height):
                raise Fail(f"{contour['name']}: the point [{x:g}, {y:g}] is outside the {width} x {height} picture")
    views = [dict(name=str(view.get("name") or f"view-{i + 1}"), box=clamp_box(view.get("box"), width, height))
             for i, view in enumerate(plan.get("views") or [])] or auto_views(contours, nudges, dabs, width, height)
    captions = plan.get("captions") or []
    for index, caption in enumerate(captions):  # said before anything runs: a slanted push bends text
        name = caption.get("name") or f"caption #{index + 1}"
        x0, y0, x1, y1 = clamp_box(caption.get("box"), width, height, name)
        for contour in contours:
            reach = contour["size"] / 2
            near = [p for p in contour["points"] if p[4] > 3
                    and math.hypot(max(x0 - p[0], 0, p[0] - x1), max(y0 - p[1], 0, p[1] - y1)) < reach]
            if not near:
                continue
            gap = min(math.hypot(max(x0 - p[0], 0, p[0] - x1), max(y0 - p[1], 0, p[1] - y1)) for p in near)
            if any(abs(p[3]) > 0.2 for p in near):
                warn(f"{contour['name']} reaches the text \"{name}\" with pushes that are not level: its brush radius is "
                     f"{reach:.0f} px and its nearest point is {gap:.0f} px from the text. Give that part "
                     "\"direction\": [1, 0] or [-1, 0], or end its edge before the text (with \"ease_end\": true)")
            elif gap < 0.6 * reach:
                say(f"[NOTE] {contour['name']}'s brush (radius {reach:.0f} px) reaches the text \"{name}\" ({gap:.0f} px "
                    f"away). Its pushes are level, so letters there are only squeezed a little. To leave the text "
                    f"untouched, end the edge at least {reach:.0f} px away from it")

    groups = {}
    for contour in contours:  # the two runs of one part are one edge
        groups.setdefault(contour["name"].split(" (")[0], []).append(contour)
    for name, runs in groups.items():
        if runs[0].get("part") not in ("elbow", "knee", "wrist", "ankle"):
            continue
        span = runs[0]["width"]
        for end in (runs[0]["points"][0], runs[-1]["points"][-1]):
            best = None
            for other, limb in groups.items():
                if limb[0].get("part") in ("upper_arm", "forearm", "thigh", "calf"):
                    for tip in (limb[0]["points"][0], limb[-1]["points"][-1]):
                        distance = math.hypot(tip[0] - end[0], tip[1] - end[1])
                        if best is None or distance < best[0]:
                            best = (distance, other)
            if best and 0.6 * span < best[0] < 4 * span:
                say(f"[NOTE] {best[0]:.0f} px of outline between the end of `{best[1]}` and `{name}` is not traced, so "
                    f"it stays as it is. If the limb's edge is visible there (an edge against clothing counts), "
                    f"extend `{best[1]}`'s edge up to the joint")

    previous = load_json(sidecar, "last run's record") if os.path.exists(sidecar) else {}
    for path, key in ((out, "jpg"), (pcraft, "pcraft")):
        if path and os.path.exists(path) and not args.force and (previous.get("written") or {}).get(key) != stamp(path):
            raise Fail(f"{path} exists and is not as this script last wrote it (edited in PhotoCraft, or someone "
                       "else's file). Nothing written. Add --force to replace it, or choose another --out")

    os.makedirs(checks, exist_ok=True)
    for name in os.listdir(checks):  # this script's own folder: nothing from an earlier plan stays to mislead
        if name.endswith(".jpg") or name == "report.json":
            os.remove(os.path.join(checks, name))

    def view_tiles(view, after=None):
        """The view's crop with the plan drawn on it, and the same crop of `after`; dark crops shown lighter."""
        x0, y0, x1, y1 = view["box"]
        before, scale = fit(photo[y0:y1, x0:x1].copy(), 1100)
        tiles = [before] + ([fit(after[y0:y1, x0:x1], 1100)[0]] if after is not None else [])
        tiles, lighter = lift(tiles)
        draw_plan(tiles[0], contours, nudges, dabs, view["box"], scale)
        label(tiles[0], f"{view['name']}: plan  x {x0}-{x1}  y {y0}-{y1}" + ("  (shown lighter)" if lighter else ""))
        if after is not None:
            label(tiles[1], "after" + ("  (shown lighter)" if lighter else ""))
        return tiles

    if args.plan_only:
        for contour in contours:
            most = max(p[4] for p in contour["points"])
            share = f", {100 * most / contour['width']:.1f}% of its width" if contour.get("width") else ""
            judged = f", need \"{contour['need']}\"" if contour.get("need") else ""
            say(f"[OK] {contour['name']}: will move up to {most:.0f} px{share}{judged}")
        for dab in dabs:
            say(f"[OK] {dab['name']}: {dab['tool']} x{dab['count']} at rate {dab['rate']}")
        say("[OK] plan drawn, nothing liquified. Red dots must sit on the outline, green arrows must point into the "
            "body part, each blue ring (an edge's first point) must be at the end body-parts.md names, and each "
            "yellow x (`toward`) must be inside the part. Eyes and noses are the magenta circles.")
        for view in views:
            path = os.path.join(checks, f"{view['name']}.plan.jpg")
            write_picture(path, view_tiles(view)[0])
            say(f"[READ] {path}")
        return

    engine = Engine()
    try:
        strokes, report_rows, caption_rows, strain, marks = [], [], [], None, []
        dx = dy = None
        if contours or nudges or dabs:
            say(f"[STEP] liquify: {len(contours)} edges, {len(nudges)} nudges, {len(dabs)} dabs")
            strokes, dx, dy, shared = solve(engine, contours, nudges, dabs, width, height, args.rounds)
            for a, contour in enumerate(contours):
                cells, worst = [], 0.0
                most = max(p[4] for p in contour["points"])
                for index, (x, y, ux, uy, want) in enumerate(contour["points"]):
                    got = landed(dx, dy, x, y, ux, uy, want)
                    cells.append(f"{want:.0f}>{got:.0f}" + ("*" if (a, index) in shared else ""))
                    if want >= 5 and (a, index) not in shared:
                        worst = max(worst, want - got, got - 1.1 * most)  # short of its target, or past the part's most
                percent = ""
                if contour.get("width"):
                    percent = (f" (most {100 * max(p[4] for p in contour['points']) / contour['width']:.1f}% of width"
                               + (f", need \"{contour['need']}\"" if contour.get("need") else "") + ")")
                say(f"[OK] {contour['name']}: brush {contour['size']}, px want>got {' '.join(cells)}{percent}")
                report_rows.append(dict(name=contour["name"], part=contour.get("part"), size=contour["size"],
                                        want=[round(p[4], 1) for p in contour["points"]],
                                        got=[round(landed(dx, dy, *p), 1) for p in contour["points"]],
                                        worst_miss=round(worst, 1)))
                if worst > max(8.0, 0.2 * most):
                    warn(f"{contour['name']}: an edge point landed {worst:.0f} px off its target")
            if shared:
                say("[NOTE] * = inside another part's brush: both pushes land there, the sum is what you see")
            for nudge in nudges:
                say(f"[OK] {nudge['name']}: one push of {nudge['px']:.0f} px, brush {nudge['size']}")
            for dab in dabs:
                say(f"[OK] {dab['name']}: {dab['tool']} x{dab['count']} at rate {dab['rate']}, brush {dab['size']}")
            # How much of the photo one result pixel covers, along x and along y: under 1 the photo is shown
            # stretched there (0.5 = twice as large), over 1 squeezed. Stretching is what shows: it smears texture.
            cover_x = (1 - np.gradient(dx, axis=1))[3:-3, 3:-3]  # the outermost rows are the gradient's own edge
            cover_y = (1 - np.gradient(dy, axis=0))[3:-3, 3:-3]
            stretched = 1 / max(0.02, float(min(cover_x.min(), cover_y.min())))
            squeezed = 1 / max(1.0, float(max(cover_x.max(), cover_y.max())))
            strain = {"stretched": round(stretched, 2), "squeezed": round(squeezed, 2)}
            say(f"[OK] moved at most {float(np.hypot(dx, dy).max()):.0f} px; at the worst place the picture is "
                f"stretched {stretched:.1f}x, at another squeezed to {squeezed:.2f}x (1 = untouched)")
            for array in (cover_x, cover_y):  # where, and by which part
                for pick in (np.argmin, np.argmax):
                    sy, sx = np.unravel_index(pick(array), array.shape)
                    value = float(array[sy, sx])
                    sy, sx = sy + 3, sx + 3
                    if value >= 0.4 and value <= 2.5:
                        continue
                    word = f"stretched {1 / max(value, 0.02):.1f}x" if value < 1 else f"squeezed to {1 / value:.2f}x"
                    if any(math.hypot(sx - m[0], sy - m[1]) < 150 for m in marks):
                        continue
                    marks.append((int(sx), int(sy), word))
                    near = min(contours, key=lambda c: min(math.hypot(sx - q[0], sy - q[1]) for q in c["points"])) \
                        if contours else None
                    where = ""
                    if near is not None:
                        push = max(q[4] for q in near["points"])
                        where = (f", beside `{near['name']}` (brush {near['size']}, pushing up to {push:.0f} px). "
                                 f"Harmless where both sides of that edge are plain; otherwise give that part a "
                                 f"`brush` of about {even_size(BRUSH_PER_PUSH * push)} or a lower `strength`")
                    warn(f"the picture is {word} at ({int(sx)}, {int(sy)}), circled in field.jpg{where}")
            if captions:
                caption_rows = caption_check(photo, dx, dy, captions)
                for row in caption_rows:
                    if row["glyphs"] < 20:
                        say(f"[NOTE] {row['name']}: no text found in its box")
                        continue
                    line = (f"{row['name']}: glyphs moved up to {row['moved_max']} px ({row['moved_share']}% of them "
                            f"over 1 px), up/down {row['up_down_max']} px, lean up to {row['lean_max']} deg "
                            f"({row['lean_share']}% over 3 deg), squeeze up to {row['squeeze_max']}%")
                    if row["up_down_max"] > 2 or row["lean_share"] > 6:
                        warn(line + ". The text will look bent. The edge that reaches it must end further away (one "
                                    "brush radius from the text, with \"ease_end\" or \"ease_start\") or get a smaller "
                                    "`brush`; and it must push level (\"direction\": [1, 0] or [-1, 0])")
                    else:
                        say(f"[OK] {line}")
        staged = engine.path(staged_name(photo_path))
        shutil.copyfile(photo_path, staged)
        staged_jpg = engine.path("result.jpg")
        staged_pcraft = engine.path(os.path.basename(pcraft)) if pcraft else None
        if staged_pcraft and os.path.abspath(staged_pcraft) == os.path.abspath(staged):
            staged_pcraft = engine.path("result.pcraft")
        say("[STEP] building the document: " + ("its layers" if layered else "the photo") + " below"
            + (", a stamp of what is visible above with the liquify as its smart filter" if strokes else "")
            + (f", {len(layers)} tone layers on top" if layers else ""))
        build_document(engine, staged, strokes, layers, staged_jpg, staged_pcraft, args.quality, layered)
        result = read_picture(staged_jpg)
        if result.shape != photo.shape:
            raise Fail("the engine's picture is not the photo's size; nothing written")

        tiles = [fit(photo, 1100)[0], fit(result, 1100)[0]]
        label(tiles[0], "before")
        label(tiles[1], "after")
        if args.reference:
            tiles.append(fit(read_source(args.reference), 1100)[0])
            label(tiles[2], "reference")
        pictures = [os.path.join(checks, "overview.jpg")]
        write_picture(pictures[0], side_by_side(tiles))
        for view in views:
            pictures.append(os.path.join(checks, f"{view['name']}.jpg"))
            write_picture(pictures[-1], side_by_side(view_tiles(view, result)))
        faces = face_boxes(contours, dabs, width, height) if dx is not None else []
        for number, (x0, y0, x1, y1) in enumerate(faces, 1):  # nothing drawn on these: they are for the look
            tiles, lighter = lift([fit(photo[y0:y1, x0:x1].copy(), 1100)[0], fit(result[y0:y1, x0:x1].copy(), 1100)[0]])
            label(tiles[0], "before" + ("  (shown lighter)" if lighter else ""))
            label(tiles[1], "after" + ("  (shown lighter)" if lighter else ""))
            pictures.append(os.path.join(checks, "face.jpg" if len(faces) == 1 else f"face-{number}.jpg"))
            write_picture(pictures[-1], side_by_side(tiles))
        if dx is not None:
            pictures.append(os.path.join(checks, "field.jpg"))
            write_picture(pictures[-1], heat_picture(photo, dx, dy, marks))
        moved_captions = [row for row in caption_rows if row.get("moved_max", 0) > 1]
        if moved_captions:
            strips = []
            for row in moved_captions:
                x0, y0, x1, y1 = row["box"]
                for picture, tag in ((photo, "before"), (result, "after")):
                    strip = fit(picture[y0:y1, x0:x1], 1800)[0]
                    label(strip, f"{row['name']} {tag}", (8, 20), 0.55)
                    strips.append(strip)
            wide = max(strip.shape[1] for strip in strips)
            strips = [np.hstack([s, np.zeros((s.shape[0], wide - s.shape[1], 3), np.uint8)]) for s in strips]
            pictures.append(os.path.join(checks, "captions.jpg"))
            write_picture(pictures[-1], np.vstack(strips))

        place(staged_jpg, out)
        written = {"jpg": stamp(out)}
        if pcraft:
            place(staged_pcraft, pcraft)
            written["pcraft"] = stamp(pcraft)
        record = {"photo": photo_path, "plan": plan, "strength": args.strength, "tone": tone, "written": written}
        with open(sidecar, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=1)
        with open(os.path.join(checks, "report.json"), "w", encoding="utf-8") as handle:
            json.dump({"edges": report_rows, "captions": caption_rows, "local_scale": strain,
                       "strokes": len(strokes), "warnings": list(_warned)}, handle, ensure_ascii=False, indent=1)
    finally:
        engine.close()
    say("[OK] check pictures written. Read every one of them before telling anyone it is done:")
    for path in pictures:
        say(f"[READ] {path}")
    if strokes:
        say("[NOTE] no fault in the edges is not yet a good picture. Then judge the look on overview.jpg"
            + (" and face.jpg" if faces else "") + " (SKILL.md step 7): still this person, nothing that looks edited, "
            "the parts in proportion, and each part better than before. Where it is not, lower that part's need")
    size = f", {os.path.basename(pcraft)} ({os.path.getsize(pcraft) / 1e6:.0f} MB, layers kept)" if pcraft else ""
    say(f"[SUCCESS] {out}{size}; plan kept in {os.path.basename(sidecar)}; {time.time() - started:.1f}s")


# ==========================================
# tone
# ==========================================

def to_lab(rgb):
    pixel = np.array([[rgb[::-1]]], np.float32) / 255
    return [float(v) for v in cv2.cvtColor(pixel, cv2.COLOR_BGR2Lab)[0, 0]]


def to_rgb(lab):
    pixel = cv2.cvtColor(np.array([[lab]], np.float32), cv2.COLOR_Lab2BGR)[0, 0]
    return [float(np.clip(v * 255, 0, 255)) for v in pixel[::-1]]


def hue_of(rgb):
    hsv = cv2.cvtColor(np.array([[rgb[::-1]]], np.float32) / 255, cv2.COLOR_BGR2HSV)[0, 0]
    return float(hsv[0])


def median_rgb(image, box):
    x0, y0, x1, y1 = box
    return [float(np.median(image[y0:y1, x0:x1, channel])) for channel in (2, 1, 0)]


def tone_curves(pairs):
    """Per channel a drawn curve (256 output levels) through the measured pairs, from 0 to 255, always rising.
    Drawn, not a point curve: a spline through close points swings between them and tints everything else."""
    curves = {}
    levels = np.arange(256, dtype=np.float64)
    for channel, name in enumerate(("red", "green", "blue")):
        merged = []
        for value, out in sorted((source[channel], target[channel]) for source, target in pairs):
            if merged and value - merged[-1][0] < 14:
                count = merged[-1][2] + 1
                merged[-1] = [(merged[-1][0] * merged[-1][2] + value) / count,
                              (merged[-1][1] * merged[-1][2] + out) / count, count]
            else:
                merged.append([value, out, 1])
        inside = [(value, out - value) for value, out, _ in merged if 6 < value < 249]
        shift = np.interp(levels, [0.0] + [v for v, _ in inside] + [255.0], [0.0] + [d for _, d in inside] + [0.0])
        shift = np.convolve(np.concatenate([np.full(7, shift[0]), shift, np.full(7, shift[-1])]), np.ones(15) / 15,
                            mode="valid")  # round the corners
        curve = levels + shift
        for index in range(1, 256):  # never flatter than a third nor steeper than 2.5: no banding
            curve[index] = min(max(curve[index], curve[index - 1] + 0.35), curve[index - 1] + 2.5)
        curve = np.clip(curve, 0, 255)
        if float(np.abs(curve - levels).max()) > 0.6:
            curves[name] = [round(float(v), 2) for v in curve]
    if curves:
        curves["pencil"] = True
    return curves


def swatch_run(engine, colours, steps, name="swatches"):
    """What PhotoCraft makes of some colours: a strip of swatches through image.adjustments.* commands."""
    strip = np.zeros((16, 16 * len(colours), 3), np.uint8)
    for index, rgb in enumerate(colours):
        strip[:, index * 16:(index + 1) * 16] = [int(round(v)) for v in rgb[::-1]]
    source, out = engine.path(f"{name}.png"), engine.path(f"{name}-out.png")
    write_raw(source, strip)
    engine.session.call("doc_open", {"path": source})
    try:
        for command, params in steps:
            engine.session.command(command, params)
        engine.session.call("doc_save", {"path": out})
    finally:
        engine.session.call("doc_close")
    image = read_raw(out, cv2.IMREAD_COLOR)
    if image is None:
        raise Fail("the engine did not hand back the swatches")
    return [[float(v) for v in image[8, index * 16 + 8][::-1]] for index in range(len(colours))]


def hue_range(surround_hues, subject_hues):
    """[zero, full, full, zero] degrees round the surroundings' hues, stopping short of the subject's; None when the
    two cannot be told apart by hue."""
    centre = math.degrees(math.atan2(sum(math.sin(math.radians(h)) for h in surround_hues),
                                     sum(math.cos(math.radians(h)) for h in surround_hues)))

    def near(hue):
        return (hue - centre + 180) % 360 - 180
    low, high = min(near(h) for h in surround_hues), max(near(h) for h in surround_hues)
    margin = 4.0  # skin is never one hue: stay this far from the measured patches
    below = [near(h) + margin for h in subject_hues if near(h) < low]
    above = [near(h) - margin for h in subject_hues if near(h) > high]
    if any(low <= near(h) <= high for h in subject_hues):
        return None
    low_zero = max(below) if below and max(below) > low - 40 else low - 40
    high_zero = min(above) if above and min(above) < high + 70 else high + 70
    low_full, high_full = min(low - 1, low_zero + 10), max(high + 1, high_zero - 30)
    if low_full - low_zero < 5 or high_zero - high_full < 5:
        return None
    return [round((v + centre) % 360, 1) for v in (low_zero, low_full, high_full, high_zero)]


def command_tone(args):
    need_libraries()
    photo, reference = read_source(args.photo), read_source(args.reference)
    if not 0 <= args.surroundings <= 1:
        raise Fail("--surroundings is between 0 and 1")
    spec = load_json(args.pairs, "pairs file")
    pairs = spec.get("pairs") if isinstance(spec, dict) else None
    if not isinstance(pairs, list) or len(pairs) < 2:
        raise Fail("a pairs file is {\"pairs\": [{\"name\": \"cheek\", \"photo\": [x0, y0, x1, y1], \"reference\": "
                   "[x0, y0, x1, y1]}, ...]} with at least two pairs (skin and something white or grey)")
    surroundings = spec.get("surroundings") or []
    rows = []
    for group, entries in (("subject", pairs), ("surroundings", surroundings)):
        for index, entry in enumerate(entries):
            name = entry.get("name") or f"{group} #{index + 1}"
            a = clamp_box(entry.get("photo"), photo.shape[1], photo.shape[0], f"{name} (photo)")
            b = clamp_box(entry.get("reference"), reference.shape[1], reference.shape[0], f"{name} (reference)")
            if min(a[2] - a[0], a[3] - a[1], b[2] - b[0], b[3] - b[1]) < 30:
                warn(f"{name}: a patch under 30 px a side is too small to trust; 60-200 px is right (lips can be less)")
            source, wanted = median_rgb(photo, a), median_rgb(reference, b)
            lab_source, lab_wanted = to_lab(source), to_lab(wanted)
            # The reference's colour at the photo's own lightness. Surroundings are rarely the same thing in both
            # pictures (a curtain here, a wall there), so they go only part of the way.
            pull = 1.0 if group == "subject" else args.surroundings
            target_lab = [lab_source[0], lab_source[1] + pull * (lab_wanted[1] - lab_source[1]),
                          lab_source[2] + pull * (lab_wanted[2] - lab_source[2])]
            rows.append(dict(name=name, group=group, boxes=(a, b), source=source, reference=wanted,
                             lab_source=lab_source, lab_reference=lab_wanted, target=to_rgb(target_lab),
                             target_lab=target_lab))
    out_stem = os.path.splitext(os.path.abspath(args.out))[0]
    tiles = []
    for row in rows:
        (ax0, ay0, ax1, ay1), (bx0, by0, bx1, by1) = row["boxes"]
        pair = [cv2.resize(photo[ay0:ay1, ax0:ax1], (200, 200), interpolation=cv2.INTER_AREA),
                cv2.resize(reference[by0:by1, bx0:bx1], (200, 200), interpolation=cv2.INTER_AREA)]
        label(pair[0], row["name"][:18], (4, 16), 0.5)
        label(pair[0], "photo", (4, 190), 0.5)
        label(pair[1], "reference", (4, 190), 0.5)
        tiles.append(np.vstack(pair))
    write_picture(out_stem + ".patches.jpg", np.hstack(tiles))

    subject = [row for row in rows if row["group"] == "subject"]
    around = [row for row in rows if row["group"] == "surroundings"]
    curves = tone_curves([(row["source"], row["target"]) for row in subject])
    engine = Engine()
    try:
        colours = [row["source"] for row in rows]

        def error(result, selection):
            return float(np.mean([math.hypot(to_lab(result[i])[1] - rows[i]["target_lab"][1],
                                             to_lab(result[i])[2] - rows[i]["target_lab"][2]) for i in selection]))
        curve_step = [("image.adjustments.curves", curves)] if curves else []
        layers = []
        if around:
            where = [i for i, row in enumerate(rows) if row["group"] == "surroundings"]
            coloured = [row["source"] for row in subject if max(row["source"]) - min(row["source"]) > 0.12 * max(row["source"])]
            band = hue_range([hue_of(rows[i]["source"]) for i in where], [hue_of(rgb) for rgb in coloured])
            before = error(swatch_run(engine, colours, curve_step), where)
            if band is None:
                say("[NOTE] the surroundings and the subject share a hue: they cannot be told apart, so no layer for "
                    "the surroundings (the curves still act on everything)")
            elif before < 2.0:
                say(f"[NOTE] the curves alone bring the surroundings within {before:.1f} of the reference: no extra "
                    "layer for them")
            else:
                centre = (band[1] + ((band[2] - band[1]) % 360) / 2) % 360
                named = min((("reds", 0), ("yellows", 60), ("greens", 120), ("cyans", 180), ("blues", 240),
                             ("magentas", 300)), key=lambda item: min(abs(centre - item[1]), 360 - abs(centre - item[1])))[0]
                best = (before, 0, 0)
                for hue in range(-24, 25, 4):
                    for saturation in range(0, -61, -10):
                        if hue == 0 and saturation == 0:
                            continue
                        step = ("image.adjustments.hueSaturation",
                                {named: {"hue": hue, "saturation": saturation, "lightness": 0, "range": band}})
                        score = error(swatch_run(engine, colours, [step] + curve_step), where) \
                            + 0.03 * abs(hue) + 0.012 * abs(saturation)
                        if score < best[0] - 0.05:
                            best = (score, hue, saturation)
                if best[1] or best[2]:
                    layers.append({"command": "layer.newAdjustmentLayer.hueSaturation", "name": "色调 · 环境",
                                   "params": {named: {"hue": best[1], "saturation": best[2], "lightness": 0,
                                                      "range": band}}})
                    say(f"[OK] surroundings: {named} range {band}, hue {best[1]:+d}, saturation {best[2]:+d}")
        if curves:
            layers.append({"command": "layer.newAdjustmentLayer.curves", "name": "色调 · 肤色与白", "params": curves})
        if not layers:
            raise Fail("the photo already matches the reference on these patches: no tone layers needed")
        steps = [(layer["command"].replace("layer.newAdjustmentLayer.", "image.adjustments."), layer["params"])
                 for layer in layers]
        greys = [[64.0] * 3, [128.0] * 3, [200.0] * 3]
        result = swatch_run(engine, colours + greys, steps)
        grey_lab = [to_lab(rgb) for rgb in result[len(colours):]]
        result = result[:len(colours)]
    finally:
        engine.close()
    table = []
    say("[OK] Lab (lightness / red-green / yellow-blue): photo -> with the tone layers | reference")
    for row, after in zip(rows, result):
        lab_after = to_lab(after)
        miss = math.hypot(lab_after[1] - row["target_lab"][1], lab_after[2] - row["target_lab"][2])
        say("     {:<14s} {:5.1f}/{:5.1f}/{:5.1f} -> {:5.1f}/{:5.1f}/{:5.1f} | {:5.1f}/{:5.1f}/{:5.1f}   off target by {:.1f}{}"
            .format(row["name"][:14], *row["lab_source"], *lab_after, *row["lab_reference"], miss,
                    "" if row["group"] == "subject" else f"  (surroundings: {args.surroundings:g} of the way)"))
        table.append(dict(name=row["name"], group=row["group"], photo=[round(v, 1) for v in row["lab_source"]],
                          toned=[round(v, 1) for v in lab_after], reference=[round(v, 1) for v in row["lab_reference"]],
                          colour_off=round(miss, 1)))
        if row["group"] == "subject" and miss > 4:
            warn(f"{row['name']}: still {miss:.1f} from the reference. Is each patch really the same thing in both "
                 f"pictures? Look at {os.path.basename(out_stem)}.patches.jpg")
        if row["group"] == "subject" and abs(row["lab_source"][0] - row["lab_reference"][0]) > 12:
            warn(f"{row['name']}: the two patches differ in lightness by "
                 f"{abs(row['lab_source'][0] - row['lab_reference'][0]):.0f} (first number of photo and reference): "
                 "one is in shadow or blown out. Pick two places lit alike")
    say("[OK] what the layers do to neutral grey (dark / middle / light), red-green and yellow-blue: "
        + "  ".join(f"{lab[1]:+.1f}/{lab[2]:+.1f}" for lab in grey_lab)
        + "   (minus yellow-blue = cooler, plus red-green = pinker)")
    cast = max(math.hypot(lab[1], lab[2]) for lab in grey_lab)
    if cast > 12:
        warn(f"the layers tint neutral grey by {cast:.0f}: the whole picture will take that colour. A pair is probably "
             "not the same thing in both pictures (check the white or grey one first)")
    if around and not any("hueSaturation" in layer["command"] for layer in layers):
        say("[NOTE] no separate layer for the surroundings: the room keeps its own colour apart from what the curves "
            "do. For surroundings pick the big area whose colour cast you want gone (a warm wall, a curtain, wood), "
            "not something grey")
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump({"layers": layers, "measured": table}, handle, ensure_ascii=False, indent=1)
    say(f"[OK] patches as measured: {out_stem}.patches.jpg (read it: each pair must show the same thing)")
    say(f"[SUCCESS] {args.out}: {len(layers)} tone layers; use it with apply --tone")


# ==========================================
# grid, main
# ==========================================

def command_grid(args):
    image = read_source(args.photo)
    view, scale, step = grid_view(image, args.box, args.side, args.step, args.brighten)
    write_picture(args.out, view, 90)
    height, width = image.shape[:2]
    dark = float(cv2.cvtColor(view, cv2.COLOR_BGR2GRAY).mean()) < 60 and not args.brighten
    say(f"[SUCCESS] {args.out}: {view.shape[1]} x {view.shape[0]}, grid every {step} px; a label x1200 or y3400 is "
        f"that picture pixel (the picture is {width} x {height}; this view is at {scale:.2f}x)"
        + ("; shown lighter than the photo" if args.brighten else "")
        + ("\n[NOTE] this view is dark: add --brighten to see edges in the shadows" if dark else ""))


def command_probe(args):
    """The strongest steps in lightness along one row or column: a number to set beside what the eye read."""
    image = read_source(args.photo)
    height, width = image.shape[:2]
    light = cv2.cvtColor(image.astype(np.float32) / 255, cv2.COLOR_BGR2Lab)[:, :, 0]
    if (args.row is None) == (args.col is None):
        raise Fail("give --row Y (to look along x) or --col X (to look along y), one of them")
    if args.row is not None:
        y = int(min(height - 1, max(0, args.row)))
        line, limit, axis = light[max(0, y - 6):y + 7].mean(axis=0), width, "x"
    else:
        x = int(min(width - 1, max(0, args.col)))
        line, limit, axis = light[:, max(0, x - 6):x + 7].mean(axis=1), height, "y"
    first = int(max(0, args.start or 0))
    last = int(min(limit, args.end or limit))
    if last - first < 40:
        raise Fail("--from and --to must be at least 40 px apart, inside the picture")
    smooth = np.convolve(np.pad(line, 6, mode="edge"), np.ones(13) / 13, mode="valid")
    step = np.zeros_like(smooth)
    step[8:-8] = smooth[16:] - smooth[:-16]  # lightness 8 px after minus 8 px before
    found, strength = [], np.abs(step[first:last]).copy()
    while len(found) < 5 and strength.max() >= 4:
        at = int(strength.argmax())
        found.append(first + at)
        strength[max(0, at - 25):at + 26] = 0
    if not found:
        say(f"[SUCCESS] no clear step in lightness between {axis}={first} and {axis}={last}: the edge there is soft or "
            "both sides are equally dark. Try another row or column nearby, or grid --brighten")
        return
    say(f"[SUCCESS] steps in lightness (0 black - 100 white) along {'row y=' + str(y) if args.row is not None else 'column x=' + str(x)}, "
        "strongest first. An edge of the body is where its side and the other side meet:")
    for position in found:
        before = float(smooth[max(first, position - 20):max(first + 1, position - 6)].mean())
        after = float(smooth[position + 6:position + 20].mean()) if position + 20 <= limit else float(smooth[position])
        say(f"     {axis}={position}: {before:.0f} -> {after:.0f} ({'dark to light' if after > before else 'light to dark'})")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Liquify a portrait and match its tone with PhotoCraft, headless")
    commands = parser.add_subparsers(dest="command", required=True)
    grid = commands.add_parser("grid", help="a view of the photo with a pixel grid, to read positions from")
    grid.add_argument("photo")
    grid.add_argument("--out", required=True, help="the view to write (.jpg)")
    grid.add_argument("--box", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), help="only this part")
    grid.add_argument("--side", type=int, default=1500, help="longest side of the view (default 1500)")
    grid.add_argument("--step", type=int, help="grid spacing in picture pixels (default: about 12 lines)")
    grid.add_argument("--brighten", action="store_true", help="show the view lighter, to see edges in dark areas")
    probe = commands.add_parser("probe", help="where lightness steps along a row or column: to pin down an edge")
    probe.add_argument("photo")
    probe.add_argument("--row", type=float, metavar="Y", help="look along this row (prints x positions)")
    probe.add_argument("--col", type=float, metavar="X", help="look along this column (prints y positions)")
    probe.add_argument("--from", dest="start", type=float, help="start of the stretch to look at")
    probe.add_argument("--to", dest="end", type=float, help="end of the stretch to look at")
    apply = commands.add_parser("apply", help="liquify by a plan and/or add tone layers")
    apply.add_argument("photo")
    apply.add_argument("--plan", help="the plan (JSON): parts, captions; see SKILL.md")
    apply.add_argument("--tone", help="tone layers (JSON) from the tone command or a saved one")
    apply.add_argument("--strength", type=float, default=1.0, help="all pushes times this (default 1; 0.7 milder)")
    apply.add_argument("--out", help="the result (.jpg); default PHOTO_液化.jpg beside the photo")
    apply.add_argument("--no-pcraft", action="store_true", help="only the .jpg, no layered .pcraft")
    apply.add_argument("--reference", help="a picture to show beside the result in overview.jpg")
    apply.add_argument("--plan-only", action="store_true", help="only draw the plan on the photo, to check positions")
    apply.add_argument("--rounds", type=int, default=5, help="corrections against the measured field (default 5)")
    apply.add_argument("--quality", type=int, default=97, help="JPEG quality (default 97)")
    apply.add_argument("--force", action="store_true", help="replace a result that was changed since it was written")
    tone = commands.add_parser("tone", help="tone layers that bring the photo's colours to a reference's")
    tone.add_argument("photo")
    tone.add_argument("reference")
    tone.add_argument("--pairs", required=True, help="matching patches in both pictures (JSON)")
    tone.add_argument("--out", required=True, help="the tone file to write (.json)")
    tone.add_argument("--surroundings", type=float, default=0.6,
                      help="how far the surroundings go toward the reference's, 0-1 (default 0.6)")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    {"grid": command_grid, "probe": command_probe, "apply": command_apply, "tone": command_tone}[args.command](args)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 out whatever the system's encoding: names here are Chinese
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        main(sys.argv[1:])
    except (Fail, McpError) as error:
        say(f"[Error] {error}")
        sys.exit(1)
    except KeyboardInterrupt:
        say("[Error] interrupted")
        sys.exit(1)
    except Exception as error:  # noqa: BLE001 — keep the [Error] contract
        say(f"[Error] {type(error).__name__}: {error}")
        sys.exit(1)
