#!/usr/bin/env python3
"""Build layered portrait-retouching documents for PhotoCraft (.pcraft), headless.

The retouching is compositor-retouch's own: its scripts/retouch_builder.py (in the folder above this skill) computes
every layer into a scratch .comp project, and this script rebuilds those layers in a PhotoCraft document through
`photocraft-cli serve`, which has no window. How each kind of layer maps onto PhotoCraft, and where the two apps
differ, is in references/mapping.md.
"""
import argparse
import importlib.util
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.realpath(__file__))  # real path: the skill may be installed as a symlink
sys.path.insert(0, HERE)
from photocraft import LINK_FILE, Serve, ServeError, connect_app, find_cli, outside_root, refused_name  # noqa: E402

SKILL_DIR = os.path.dirname(HERE)
BUILDER = os.environ.get("RETOUCH_BUILDER") or os.path.join(os.path.dirname(SKILL_DIR), "scripts", "retouch_builder.py")
# The documents whose PhotoCraft tab is older than the file (see stale_tabs); tests keep theirs elsewhere.
STALE_FILE = os.environ.get("PCRAFT_RETOUCH_STALE") or os.path.join(os.path.dirname(LINK_FILE), "photocraft-stale.json")

# Which builder group (retouch_builder.GROUPS) each module's layers belong to.
MODULE_GROUP = {"freq_sep": "freq_sep", "delicious_skin": "skin", "sculpt": "light", "neutral_gray": "light",
                "dual_curves": "light", "eyes": "features", "look": "look", "glow": "glow", "grain": "grain",
                "inspection": "inspection"}
# What a module already made, by how the layer or folder name begins (as the builder skips them).
MADE_BY = {"freq_sep": ("🎚️", "低频", "高频"), "sculpt": ("🗿",), "neutral_gray": ("🩶",), "eyes": ("👁️",),
           "grain": ("🎞️",), "inspection": ("🔍",)}
DUAL_CURVES = ("☀️", "🌑")
# Smart filters that make a stamped Smart Object (a snapshot of what lies below, see Converter.stamped).
STAMPED_FILTERS = {"filter.other.highPass", "filter.sharpen.unsharpMask", "filter.blur.gaussianBlur"}
# Pointwise Compositor adjustments PhotoCraft draws differently: baked into a 3D LUT (Color Lookup) instead.
LUT_KINDS = {"Hue/Saturation", "Color Balance", "Levels", "Exposure", "Gradient Map", "Invert"}
LUT_SIZE = 33
CURVE_POINTS = 19  # PhotoCraft's limit per curve

_warned = set()


class Fail(Exception):
    pass


def say(message):
    print(message, flush=True)


def warn(message, once=False):
    if once and message in _warned:
        return
    _warned.add(message)
    say(f"[WARN] {message}")


def load_builder():
    if not os.path.isfile(BUILDER):
        raise Fail(f"retouch_builder.py not found at {BUILDER}. This skill sits in compositor-retouch/photocraft-retouch "
                   "and uses that skill's script; set RETOUCH_BUILDER to point elsewhere")
    sys.dont_write_bytecode = True  # read the builder without writing its __pycache__
    spec = importlib.util.spec_from_file_location("retouch_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as error:
        raise Fail(f"{error}: python3 -m pip install \"opencv-python-headless<5\" pillow numpy") from error
    return module


# ==========================================
# Arguments (the builder's, with --pcraft for --comp)
# ==========================================

def parse_args(argv, RB):
    p = argparse.ArgumentParser(description="Build layered portrait-retouching documents for PhotoCraft (.pcraft), "
                                            "headless, with compositor-retouch's modules")
    p.add_argument("--pcraft", required=True, help="the .pcraft document to create or add to")
    p.add_argument("--init", metavar="PHOTO", help="create the document from this photo (JPEG, PNG or TIFF)")
    p.add_argument("--force", action="store_true", help="with --init, replace an existing document")
    p.add_argument("--preset", choices=sorted(RB.PRESETS), help="daily, or for a new document dr5 or studio")
    p.add_argument("--engine", choices=sorted(RB.SMOOTH_ENGINES), default="form")
    p.add_argument("--texture", choices=sorted(RB.TEXTURES), default="natural")
    p.add_argument("--blur-radius", type=RB.blur_radius_arg, metavar="PX|auto",
                   help="the frequency separation's radius (a new pair only: PhotoCraft's is not made again)")
    p.add_argument("--strength", type=float, default=1.0, help="0.1-2: glow and look strength (and smoothing)")
    p.add_argument("--skin-strength", type=float, help="0.1-2: the smoothing's own strength")
    p.add_argument("--refresh", action="store_true", help="recompute the smoothing and re-stamp sculpt, eye "
                   "brilliance and soft focus from the document as it is now (save it in PhotoCraft first)")
    p.add_argument("--freq-sep", action="store_true")
    # As the builder's: no `choices`, which Python before 3.12 checks the bare flag's const against.
    p.add_argument("--delicious-skin", nargs="?", const="default", metavar="auto|manual",
                   help="black mask to brush on (default), or auto: masked to the skin found; left unsaid, a document "
                        "that has one kind keeps it")
    p.add_argument("--sculpt", action="store_true")
    p.add_argument("--neutral-gray", action="store_true")
    p.add_argument("--dual-curves", action="store_true")
    p.add_argument("--eyes", action="store_true")
    p.add_argument("--look", choices=sorted(RB.LOOKS))
    p.add_argument("--glow", action="append", nargs="?", const="mist", metavar="STYLE[:COLOR[,COLOR]]")
    p.add_argument("--grain", action="store_true")
    p.add_argument("--inspection", action="store_true")
    p.add_argument("--no-parsing", action="store_true")
    p.add_argument("--no-vision", action="store_true")
    p.add_argument("--no-checks", action="store_true")
    p.add_argument("--render", nargs="?", const="", metavar="IMAGE",
                   help="flatten the document to a JPEG or PNG (default <name>.render.jpg)")
    p.add_argument("--without", action="append", default=[], metavar="MODULE")
    p.add_argument("--progress", action="store_true")
    args = p.parse_args(argv)

    for name in ("strength", "skin_strength"):
        value = getattr(args, name)
        if value is not None and not 0.1 <= value <= 2:
            raise Fail(f"--{name.replace('_', '-')} must be between 0.1 and 2")
    keys = [key for key, _ in RB.MODULES if key != "refresh"]
    left_out = {name.strip().lstrip("-").replace("-", "_") for name in args.without}
    if left_out - set(keys):
        raise Fail(f"--without: no module called {', '.join(sorted(left_out - set(keys)))}")
    wanted = set(RB.PRESETS.get(args.preset, [])) - left_out
    wanted.update(key for key in keys if getattr(args, key))
    if args.delicious_skin not in (None, "default", "auto", "manual"):
        raise Fail(f"--delicious-skin takes auto or manual, not {args.delicious_skin!r}")
    if "delicious_skin" in wanted and not args.delicious_skin:
        args.delicious_skin = "default"
    if args.preset in ("dr5", "studio") and not args.init:
        raise Fail(f"--preset {args.preset} builds a new document: add --init <photo>")
    if args.force and not args.init:
        raise Fail("--force only applies together with --init")
    if args.refresh and args.init:
        raise Fail("--refresh recomputes an existing document: leave out --init")
    args.modules = [key for key in keys if key in wanted]
    return args


def common_flags(args):
    flags = ["--engine", args.engine, "--texture", args.texture, "--strength", f"{args.strength:g}"]
    if args.skin_strength is not None:
        flags += ["--skin-strength", f"{args.skin_strength:g}"]
    for flag in ("no_vision", "no_parsing", "no_checks"):
        if getattr(args, flag):
            flags.append("--" + flag.replace("_", "-"))
    return flags


def module_flags(args, key):
    if key == "delicious_skin":
        return ["--delicious-skin", args.delicious_skin if args.delicious_skin in ("auto", "manual") else "manual"]
    if key == "freq_sep" and args.blur_radius is not None:  # it sizes nothing else
        radius = args.blur_radius
        return ["--freq-sep", "--blur-radius", radius if radius == "auto" else f"{radius:g}"]
    if key == "look":
        return ["--look", args.look]
    if key == "glow":
        return [part for spec in args.glow for part in ("--glow", spec)]
    return ["--" + key.replace("_", "-")]


# ==========================================
# The builder's scratch project
# ==========================================

class Comp:
    """A .comp the builder wrote: records bottom to top per parent."""

    def __init__(self, path):
        self.path = path
        self.manifest = json.load(open(os.path.join(path, "manifest.json")))
        self.width, self.height = self.manifest["width"], self.manifest["height"]
        self.kids = {}
        for record in self.manifest["layers"]:
            self.kids.setdefault(record.get("parentID"), []).append(record)

    def top(self):
        return self.kids.get(None, [])

    def children(self, record):
        return self.kids.get(record["id"], [])

    def file(self, name):
        return os.path.join(self.path, "images", name)

    def walk(self, records=None):
        for record in self.top() if records is None else records:
            yield record
            yield from self.walk(self.children(record))


def run_builder(photo, comp, flags, label):
    """retouch_builder.py --init `photo` into the scratch `comp`; its warnings pass through, its errors stop the run."""
    command = [sys.executable, BUILDER, "--init", photo, "--comp", comp, *flags]
    # Its layers are read once here and deleted: on the Mac's own disk written uncompressed (but the smoothing, kept
    # in the document), several times faster; elsewhere (an external or network disk) as usual, a fifth the size.
    local = os.stat(os.path.dirname(comp)).st_dev == os.stat(tempfile.gettempdir()).st_dev
    result = subprocess.run(command, capture_output=True, text=True,
                            env=dict(os.environ, RETOUCH_PNG_LEVEL="0" if local else ""))
    for line in result.stdout.splitlines():
        if line.startswith("[WARN] "):
            warn(line[7:], once=True)
    if result.returncode != 0:
        lines = (result.stdout + "\n" + result.stderr).splitlines()
        errors = [line[8:] for line in lines if line.startswith("[Error] ")] or [line for line in lines if line][-3:]
        raise Fail(f"retouch_builder.py ({label}): {' '.join(errors)}")
    return Comp(comp)


def png_bit_depth(path):
    with open(path, "rb") as f:
        head = f.read(25)
    return head[24] if head[:8] == b"\x89PNG\r\n\x1a\n" else 8


# ==========================================
# The PhotoCraft document
# ==========================================

class Node:
    def __init__(self, raw, parent):
        self.raw = raw
        self.id = raw["id"]
        self.name = raw.get("name") or ""
        self.kind = raw.get("kind") or ""
        self.visible = raw.get("visible", True)
        self.parent = parent
        self.filters = [f.get("command") for f in raw.get("smartFilters") or []]
        self.children = [Node(child, self) for child in reversed(raw.get("children") or raw.get("layers") or [])]

    @property
    def is_group(self):
        return self.kind == "Group"

    @property
    def is_smart(self):
        return self.kind.replace(" ", "").lower() == "smartobject"

    def siblings(self, top):
        return self.parent.children if self.parent else top


class Doc:
    def __init__(self, serve, staging):
        self.s = serve
        self.staging = staging
        self.blend_names = {}

    def run(self, command, params=None):
        return self.s.run(command, params or {})

    def tree(self):
        """Top-level nodes, bottom to top."""
        doc = self.s.call("doc.inspect")
        doc = doc.get("document", doc)
        return [Node(raw, None) for raw in reversed(doc["layers"])]

    @staticmethod
    def flat(top):
        """Every node in drawing order (a folder, then what is inside it, bottom to top)."""
        out = []

        def visit(nodes):
            for node in nodes:
                out.append(node)
                visit(node.children)
        visit(top)
        return out

    def find(self, layer_id, top=None):
        return next((n for n in self.flat(top or self.tree()) if n.id == layer_id), None)

    def select(self, layer_id, mode="replace"):
        self.run("layer.select", {"layer": layer_id, "mode": mode})

    def deselect(self):
        try:
            self.run("select.deselect")
        except ServeError:
            pass  # nothing was selected

    def place(self, path, smart=False):
        r = self.s.call("doc.place", {"path": path, "scale": 100, "fit": False, "smartObject": smart})
        return r["layer"]

    def move_above(self, layer_id, anchor):
        if anchor is not None and anchor != layer_id:
            self.run("layer.moveTo", {"layer": layer_id, "target": anchor, "position": "above"})

    def move_into(self, layer_id, folder):
        self.run("layer.moveTo", {"layer": layer_id, "target": folder, "position": "into"})

    def blend(self, layer_id, name):
        tried = []
        for candidate in [self.blend_names.get(name)] + [name, name.replace(" (Add)", ""), name.replace(" ", "")]:
            if not candidate:
                continue
            try:
                self.run("layer.setProps", {"layer": layer_id, "blend": candidate})
                self.blend_names[name] = candidate
                return
            except ServeError as error:
                tried.append(str(error)[-120:])
        raise Fail(f"PhotoCraft takes none of the spellings of the blend mode {name!r}: {tried[-1]}")

    def selection_from(self, mask_png):
        """'hide', 'reveal', or loads the grey PNG (full-size) as the selection and returns 'selection'."""
        mask = np.asarray(Image.open(mask_png).convert("L"))
        if mask.min() == mask.max() and int(mask.min()) in (0, 255):
            return "hide" if mask.min() == 0 else "reveal"
        doc = self.s.call("doc.inspect")
        doc = doc.get("document", doc)
        if mask.shape != (doc["height"], doc["width"]):
            mask = np.full((doc["height"], doc["width"]), int(round(float(mask.mean()))), np.uint8)
        path = tempfile.mktemp(suffix=".png", dir=self.staging)
        Image.fromarray(mask).convert("RGB").save(path, compress_level=1)
        helper = self.place(path)
        self.run("select.loadSelection", {"channel": "red", "layer": helper})
        self.run("layer.delete", {"layer": helper})
        os.remove(path)
        return "selection"

    def mask(self, layer_id, mask_png):
        how = self.selection_from(mask_png)
        self.select(layer_id)
        self.run({"hide": "layer.layerMask.hideAll", "reveal": "layer.layerMask.revealAll"}.get(
            how, "layer.layerMask.revealSelection"), {"layer": layer_id})
        self.deselect()

    def export(self, path):
        """The flattened document at full size (doc.render previews stop at 2048 px)."""
        if os.path.exists(path):
            os.remove(path)
        self.s.call("doc.save", {"path": path})

    @contextmanager
    def only_below(self, node_id, include_self):
        """Hide what is drawn above `node_id` (and the node itself unless `include_self`), and every observation
        folder; put the eyes back afterwards."""
        top = self.tree()
        node = self.find(node_id, top)
        hidden = []
        if node is not None:
            chain, current = [], node
            while current is not None:
                chain.append(current)
                current = current.parent
            for level in chain:
                siblings = level.siblings(top)
                above = siblings[siblings.index(level) + 1:]
                hidden += [n for n in above if n.visible]
            if not include_self and node.visible:
                hidden.append(node)
        hidden += [n for n in top if n.name.startswith("🔍") and n.visible and n not in hidden]
        for n in hidden:
            self.run("layer.setProps", {"layer": n.id, "visible": False})
        try:
            yield
        finally:
            for n in hidden:
                self.run("layer.setProps", {"layer": n.id, "visible": True})

    def active_index(self):
        return self.s.call("session.list").get("active")


# ==========================================
# Compositor layers -> PhotoCraft layers
# ==========================================

def curve_points(RB, channel):
    """Compositor's shape-preserving curve as at most 19 points, which PhotoCraft's spline follows within 2 levels
    (handed the same few points, the spline overshoots by up to 58 levels)."""
    if len(channel) <= 2:
        return [[p["x"], p["y"]] for p in channel]
    xs = sorted(set(np.round(np.linspace(0, 255, CURVE_POINTS)).astype(int).tolist() + [p["x"] for p in channel]))
    xs = [xs[i] for i in np.linspace(0, len(xs) - 1, min(CURVE_POINTS, len(xs))).round().astype(int)]
    return [[int(x), float(np.round(RB._curve(channel, np.array([x]))[0], 2))] for x in xs]


def cube_text(RB, record, size=LUT_SIZE):
    """The adjustment as Compositor computes it (retouch_builder.adjusted), sampled into a .cube 3D LUT."""
    g = np.linspace(0, 1, size, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")  # .cube lists red fastest
    out = RB.adjusted(np.stack([r, gg, b], -1).reshape(1, -1, 3), record).reshape(-1, 3)
    return f"LUT_3D_SIZE {size}\n" + "".join(f"{v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n" for v in out)


class Converter:
    """Rebuilds a builder record (and what is inside a folder record) in the document, right above `anchor`."""

    def __init__(self, doc, RB, comp):
        self.doc, self.RB, self.comp = doc, RB, comp
        self.made = []  # ids of every layer made, in order

    def build(self, record, anchor, scale=1.0):
        if record.get("isGroup"):
            layer_id = self.folder(record, anchor, scale)
        elif record.get("adjustment"):
            layer_id = self.adjustment(record, anchor, scale)
        else:
            layer_id = self.pixels(record, anchor, scale)
        self.made.append(layer_id)
        return layer_id

    def finish(self, layer_id, record, scale, blend=None, opacity=None):
        """Name, opacity (a Compositor folder's opacity multiplies into each layer in it: see mapping.md), blend,
        and the eye."""
        value = (record.get("opacity", 1.0) if opacity is None else opacity) * scale
        self.doc.run("layer.setProps", {"layer": layer_id, "name": record["name"], "opacity": round(value, 4)})
        self.doc.blend(layer_id, blend or record.get("blendMode", "Normal"))
        if not record.get("isVisible", True):
            self.doc.run("layer.setProps", {"layer": layer_id, "visible": False})

    def pixels(self, record, anchor, scale):
        smart = record["name"].startswith(self.RB.SMOOTH)  # the smoothing: a Smart Object, so --refresh swaps pixels
        if anchor is not None:
            self.doc.select(anchor)
        layer_id = self.doc.place(self.comp.file(record["imageFile"]), smart=smart)
        self.doc.move_above(layer_id, anchor)
        overlay = (record.get("effects") or {}).get("colorOverlay")
        # A glow (mist, halation): Compositor recolours the layer with its colour overlay, then screens it.
        # Photoshop-style apps blend a colour overlay on its own (a gold glow pasted on, not lit) unless interior
        # effects blend as a group, an Advanced Blending option PhotoCraft composites on the CPU, for the whole
        # document on every redraw. A Screen group around the layer, drawn Normal inside, gives the same light and
        # keeps the GPU; the colour stays the layer's own overlay to change.
        glow = overlay is not None and record.get("blendMode") == "Screen"
        self.finish(layer_id, dict(record, name="光 · 正常模式（改色用这层的颜色叠加）") if glow else record, scale,
                    blend="Normal" if glow else None)
        if record.get("maskFile"):
            self.doc.mask(layer_id, self.comp.file(record["maskFile"]))
        if overlay and overlay.get("enabled", True):
            color = "#" + "".join(f"{round(overlay[c] * 255):02X}" for c in ("red", "green", "blue"))
            self.doc.select(layer_id)
            self.doc.run("layer.layerStyle.colorOverlay", {"color": color, "opacity": round(overlay.get("opacity", 1) * 100)})
        if glow:
            self.doc.select(layer_id)
            r = self.doc.run("layer.groupLayers", {"name": record["name"], "opacity": 100}) or {}
            group = r.get("layer") or self.doc.find(layer_id).parent.id
            self.doc.blend(group, "Screen")
            return group
        return layer_id

    def adjustment(self, record, anchor, scale):
        settings = record["adjustment"]
        kind = settings["kind"]
        if kind == "Tonal Contrast":
            tonal = settings["filterLayerSettings"]["tonal"]
            # High Pass at the same radius, Soft Light, matches the builder's 3D sculpt (amount 40) within its
            # effect's size: see mapping.md. Other amounts scale the layer's opacity.
            weight = min(1.0, float(tonal.get("amount", 40)) / 40.0)
            return self.stamped(record, anchor, scale, [("filter.other.highPass", {"radius": float(tonal["radius"])})],
                                "Soft Light", record.get("opacity", 1.0) * weight)
        if kind == "Sharpen":
            block = settings["filterLayerSettings"]["sharpen"]
            return self.stamped(record, anchor, scale, [("filter.sharpen.unsharpMask", {
                "amount": float(block["amount"]), "radius": float(block["radius"]), "threshold": 0})], None, None)
        if kind == "Gaussian Blur":
            return self.stamped(record, anchor, scale, [("filter.blur.gaussianBlur", {
                "radius": float(settings.get("blurRadius") or 10)})], None, None)
        if kind == "Grain":
            return self.grain(record, anchor, scale)
        if kind == "Curves":
            channels = settings["curves"]["channels"]
            command = "layer.newAdjustmentLayer.curves"
            params = {key: curve_points(self.RB, channel)
                      for key, channel in zip(("points", "red", "green", "blue"), channels)}
            name = record["name"]
        elif kind == "Black & White" and not (settings.get("blackWhiteSettings") or {}).get("tint"):
            bw = settings.get("blackWhiteSettings") or {}
            command = "layer.newAdjustmentLayer.blackWhite"
            params = {key: bw.get(key, default) for key, default in (("reds", 40), ("yellows", 60), ("greens", 40),
                                                                     ("cyans", 60), ("blues", 20), ("magentas", 80))}
            name = record["name"]
        elif kind in LUT_KINDS or kind == "Black & White":
            command = "layer.newAdjustmentLayer.colorLookup"
            params = {"data": cube_text(self.RB, settings), "interpolation": "trilinear"}
            name = f"{record['name']} · 3D LUT"
        else:
            raise Fail(f"{kind} adjustment layers ('{record['name']}') have no PhotoCraft mapping in this skill")
        how = self.doc.selection_from(self.comp.file(record["maskFile"])) if record.get("maskFile") else "reveal"
        if anchor is not None:
            self.doc.select(anchor)
        if how != "selection":
            self.doc.deselect()  # without a selection the new layer's mask is white
        layer_id = self.doc.run(command, params)["layer"]  # with one, the selection becomes its mask
        self.doc.move_above(layer_id, anchor)
        if how == "hide":
            self.doc.run("layer.layerMask.delete", {"layer": layer_id})
            self.doc.run("layer.layerMask.hideAll", {"layer": layer_id})
        self.finish(layer_id, dict(record, name=name), scale)
        return layer_id

    def stamped(self, record, anchor, scale, filters, blend, opacity):
        """A Compositor live filter layer: everything visible stamped into a Smart Object above `anchor`, with the
        filter as a smart filter. It is a snapshot: --refresh re-stamps it after work below (see mapping.md)."""
        if anchor is not None:
            self.doc.select(anchor)
        layer_id = self.doc.run("layer.stampVisible")["layer"]
        r = self.doc.run("layer.smartObjects.convertToSmartObject", {"layer": layer_id}) or {}
        layer_id = r.get("layer", layer_id)
        self.doc.move_above(layer_id, anchor)
        self.doc.select(layer_id)
        for command, params in filters:
            self.doc.run(command, params)
        self.finish(layer_id, record, scale, blend=blend, opacity=opacity)
        if record.get("maskFile"):
            self.doc.mask(layer_id, self.comp.file(record["maskFile"]))
        return layer_id

    def grain(self, record, anchor, scale):
        """Compositor's Grain: a neutral grey Overlay Smart Object with Add Noise (and a slight Gaussian Blur for
        bigger grain), the amount measured so its strength matches the builder's grain on mid grey."""
        settings = record["adjustment"].get("grainSettings") or {}
        amount, blur = self.grain_plan(settings)
        if anchor is not None:
            self.doc.select(anchor)
        layer_id = self.doc.run("layer.new.layer", {"mode": "Overlay", "fillNeutral": True})["layer"]
        r = self.doc.run("layer.smartObjects.convertToSmartObject", {"layer": layer_id}) or {}
        layer_id = r.get("layer", layer_id)
        self.doc.move_above(layer_id, anchor)
        self.doc.select(layer_id)
        self.add_noise(amount, blur, int(settings.get("seed", 1)) % 2 ** 32)
        self.finish(layer_id, record, scale, blend="Overlay")
        return layer_id

    def add_noise(self, amount, blur, seed):
        self.doc.run("filter.noise.addNoise", {"amount": round(amount, 3), "distribution": "gaussian",
                                               "monochromatic": True, "seed": seed})
        if blur > 0:
            self.doc.run("filter.blur.gaussianBlur", {"radius": round(blur, 3)})

    def grain_plan(self, settings):
        """Overlay noise is weaker away from mid grey, unlike Compositor's grain, so the amount is measured on a
        512-pixel crop from the middle of the photo itself: the builder's grain there against PhotoCraft's."""
        size = float(settings.get("size", 1.0))
        blur = 0.0 if size <= 1.15 else max(0.3, 0.3 + 0.24 * (size - 1.31))  # matches the grain's coarseness
        photo = Image.open(self.comp.file(self.comp.top()[0]["imageFile"]))
        photo = np.asarray(photo.convert("RGB")) if photo.mode != "RGB" else np.asarray(photo)
        if photo.dtype != np.uint8:
            photo = np.round(photo.astype(np.float32) / 257).astype(np.uint8)
        h, w = photo.shape[:2]
        side = min(512, h, w)
        crop = np.ascontiguousarray(photo[(h - side) // 2:(h + side) // 2, (w - side) // 2:(w + side) // 2])
        target = float((self.RB._grain(crop, dict(settings, seed=7)).astype(np.float32) - crop).std())
        probe = os.path.join(self.doc.staging, "grain-crop.png")
        Image.fromarray(crop).save(probe)
        active = self.doc.active_index()
        self.doc.s.call("doc.new", {"width": side, "height": side, "background": "transparent"})
        try:
            self.doc.place(probe)
            layer_id = self.doc.run("layer.new.layer", {"mode": "Overlay", "fillNeutral": True})["layer"]
            r = self.doc.run("layer.smartObjects.convertToSmartObject", {"layer": layer_id}) or {}
            self.doc.select(r.get("layer", layer_id))
            self.add_noise(10, blur, 7)
            path = os.path.join(self.doc.staging, "grain-probe.png")
            self.doc.export(path)
            measured = float((np.asarray(Image.open(path).convert("RGB")).astype(np.float32) - crop).std())
        finally:
            self.doc.s.call("doc.close", {})
            if active is not None:
                self.doc.s.call("doc.select", {"index": active})
        return (min(400.0, max(0.1, 10 * target / measured)) if measured > 0 else 10.0), blur

    def folder(self, record, anchor, scale):
        inner = scale * record.get("opacity", 1.0)
        children, below = [], anchor
        for child in self.comp.children(record):
            below = self.build(child, below, inner)
            children.append(below)
        if not children:
            return anchor
        for number, child in enumerate(children):
            self.doc.select(child, "replace" if number == 0 else "add")
        r = self.doc.run("layer.groupLayers", {"name": record["name"], "opacity": 100}) or {}
        folder = r.get("layer") or self.doc.find(children[0]).parent.id
        if record.get("maskFile"):
            self.doc.mask(folder, self.comp.file(record["maskFile"]))
        if not record.get("isVisible", True):
            self.doc.run("layer.setProps", {"layer": folder, "visible": False})
        return folder


# ==========================================
# The run
# ==========================================

class Run:
    def __init__(self, args, RB, dest, staging, serve):
        self.args, self.RB, self.dest, self.staging = args, RB, dest, staging
        self.doc = Doc(serve, staging)
        self.added, self.redone, self.changed = [], [], False
        self.count = 0

    def step(self, label):
        if self.args.progress:
            say(f"[STEP] {label}")

    def builder(self, photo, key, flags):
        self.count += 1
        comp = os.path.join(self.staging, f"{self.count:02d}-{key}.comp")
        result = run_builder(photo, comp, common_flags(self.args) + flags, key)  # the last of a repeated flag wins
        self.collect_checks(comp)
        return result

    def collect_checks(self, comp):
        source = comp[:-len(".comp")] + ".checks"
        if self.args.no_checks or not os.path.isdir(source):
            return
        target = os.path.splitext(self.dest)[0] + ".checks"
        os.makedirs(target, exist_ok=True)
        for name in sorted(os.listdir(source)):
            shutil.copy2(os.path.join(source, name), os.path.join(target, name))
            self.checks = getattr(self, "checks", {})
            self.checks[name] = os.path.join(target, name)

    # ---- a new document ----
    def create(self):
        args = self.args
        self.step("1/2 builder")
        flags = [flag for key in args.modules for flag in module_flags(args, key)]
        comp = self.builder(os.path.abspath(args.init), "init", flags)
        original = comp.top()[0]
        depth = 16 if png_bit_depth(comp.file(original["imageFile"])) == 16 else 8
        self.step("2/2 photocraft")
        self.doc.s.call("doc.new", {"width": comp.width, "height": comp.height, "background": "transparent",
                                    "depth": depth, "resolution": comp.manifest.get("resolution", 72),
                                    "name": os.path.splitext(os.path.basename(self.dest))[0]})
        blank = [n.id for n in self.doc.tree()]
        convert = Converter(self.doc, self.RB, comp)
        anchor = convert.build(original, None)
        for layer_id in blank:
            self.doc.run("layer.delete", {"layer": layer_id})
        for record in comp.top()[1:]:
            anchor = convert.build(record, anchor)
        self.added += convert.made
        self.changed = True

    # ---- an existing document ----
    def classify(self, node):
        return self.RB.group_of({"name": node.name, "isGroup": node.is_group})

    def anchor_for(self, group, top):
        """The existing folder of `group`, else the topmost of this skill's items that belongs below it, else the
        photo (the bottom layer): new layers go right above it."""
        folder_name = self.RB.FOLDER_NAMES.get(group)
        if folder_name:
            mark = self.RB.folder_mark(folder_name)
            folder = next((n for n in top if n.is_group and n.name.startswith(mark)), None)
            if folder:
                return folder, True
        rank = self.RB.RANK[group]
        below = [n for n in top[1:] if self.classify(n) is not None and
                 (self.RB.RANK[self.classify(n)] < rank or (not folder_name and self.RB.RANK[self.classify(n)] == rank))]
        return (below[-1] if below else top[0]), False

    def add(self, key):
        args, RB = self.args, self.RB
        top = self.doc.tree()
        names = [n.name for n in self.doc.flat(top)]
        if key in MADE_BY and any(name.startswith(MADE_BY[key]) for name in names):
            radius = " (PhotoCraft's pair isn't made again at another radius: delete the 🎚️ 高低频 group first)" \
                if key == "freq_sep" and args.blur_radius is not None else ""
            say(f"[SKIP] --{key.replace('_', '-')}: the document has it already{radius}")
            return
        if key == "look":
            label = RB.LOOKS[args.look][0]
            if any(n.is_group and n.name.startswith(f"{RB.LOOK_PREFIX} 调色 · {label}") for n in top):
                say(f"[SKIP] --look {args.look}: the document has this grade; its layers' opacity sets the strength")
                return
        if key == "dual_curves" and all(any(name.startswith(p) for name in names) for p in DUAL_CURVES):
            say("[SKIP] --dual-curves: the document has both curves")
            return
        if key == "delicious_skin":
            smoothing = [n for n in self.doc.flat(top) if n.name.startswith(RB.SMOOTH)]
            if args.delicious_skin in ("auto", "manual"):
                manual = args.delicious_skin == "manual"
            else:  # left unsaid: the kind the document has (one only), so it is made again, else brushed on by hand
                kinds = {n.name.startswith(RB.MANUAL_PREFIX) for n in smoothing}
                manual = kinds.pop() if len(kinds) == 1 else True
                args.delicious_skin = "manual" if manual else "auto"
            same = [n for n in smoothing if n.name.startswith(RB.MANUAL_PREFIX) == manual]
            if same:
                for node in same:
                    self.redo_skin(node, manual, keep_settings=False)
                return
        group = MODULE_GROUP[key]
        anchor, into = self.anchor_for(group, top)
        with self.doc.only_below(anchor.id, include_self=True):
            base = os.path.join(self.staging, f"base-{key}.png")
            self.doc.export(base)
            comp = self.builder(base, key, module_flags(args, key))
            records = comp.top()[1:]  # not the builder's copy of the photo
            if key == "dual_curves":
                present = [p for p in DUAL_CURVES if any(name.startswith(p) for name in names)]
                records = [r for r in comp.walk(records) if not r.get("isGroup") and not r["name"].startswith(tuple(present))
                           and r["name"].startswith(DUAL_CURVES)] if present else records
            convert = Converter(self.doc, RB, comp)
            if into:
                folder_mark = RB.folder_mark(RB.FOLDER_NAMES[group])
                leaves = []
                for record in records:
                    if record.get("isGroup") and record["name"].startswith(folder_mark):
                        leaves += [(child, record.get("opacity", 1.0)) for child in comp.children(record)]
                    else:
                        leaves.append((record, 1.0))
                below, made = anchor.id, []
                for record, scale in leaves:
                    below = convert.build(record, below, scale)
                    made.append(below)
                for layer_id in made:
                    self.doc.move_into(layer_id, anchor.id)
            else:
                below = anchor.id
                for record in records:
                    below = convert.build(record, below)
        self.added += convert.made
        self.changed = True

    def skin_settings(self, name):
        RB = self.RB
        engine = next((key for key, label in RB.SMOOTH_ENGINES.items() if label in name), "form")
        texture = next((key for key, (_, label) in RB.TEXTURES.items() if label in name), "natural")
        default = self.args.skin_strength or self.args.strength or 1.0
        return engine, texture, RB.recorded_strength(name, default)

    def redo_skin(self, node, manual, keep_settings):
        """Recompute a smoothing layer from what lies below it now. `keep_settings` (--refresh): as its name
        records, keeping name and opacity; otherwise (a module run again) with this run's settings, as the builder
        redoes it."""
        args = self.args
        with self.doc.only_below(node.id, include_self=False):
            base = os.path.join(self.staging, f"base-skin-{node.id}.png")
            self.doc.export(base)
        if keep_settings:
            engine, texture, strength = self.skin_settings(node.name)
            flags = ["--delicious-skin", "manual" if manual else "auto", "--engine", engine, "--texture", texture,
                     "--skin-strength", f"{strength:g}"]
        else:
            flags = ["--delicious-skin", "manual" if manual else "auto"]
        comp = self.builder(base, "refresh" if keep_settings else "delicious_skin", flags)
        record = next(r for r in comp.walk() if r["name"].startswith(self.RB.SMOOTH))
        pixels = comp.file(record["imageFile"])
        if node.is_smart:
            self.doc.s.call("doc.replaceSmartObject", {"path": pixels, "layer": node.id})
            layer_id = node.id
        else:  # rasterized in PhotoCraft: a new Smart Object takes its place and its mask
            layer_id = self.doc.place(pixels, smart=True)
            self.doc.move_above(layer_id, node.id)
            if node.raw.get("hasMask"):
                self.doc.run("select.loadSelection", {"channel": "mask", "layer": node.id})
                self.doc.select(layer_id)
                self.doc.run("layer.layerMask.revealSelection", {"layer": layer_id})
                self.doc.deselect()
            self.doc.run("layer.setProps", {"layer": layer_id, "name": node.name,
                                            "opacity": node.raw.get("opacity", 1.0)})
            self.doc.run("layer.delete", {"layer": node.id})
        if not keep_settings:
            self.doc.run("layer.setProps", {"layer": layer_id, "name": record["name"],
                                            "opacity": record.get("opacity", 1.0)})
        self.redone.append((layer_id, "重做 · 保留蒙版" if not keep_settings else "按现在的画面重算 · 蒙版不变"))
        self.changed = True

    def refresh(self):
        top = self.doc.tree()
        skins = [n for n in self.doc.flat(top) if n.name.startswith(self.RB.SMOOTH)]
        for node in skins:
            self.redo_skin(node, node.name.startswith(self.RB.MANUAL_PREFIX), keep_settings=True)
        if not skins and not self.stamped():
            say("[SKIP] --refresh: nothing in the document to recompute (smoothing or stamped Smart Objects)")
        self.restamp(set())

    def stamped(self, top=None):
        return [n for n in self.doc.flat(top or self.doc.tree()) if n.is_smart and set(n.filters) & STAMPED_FILTERS
                and "filter.noise.addNoise" not in n.filters]

    def restamp(self, skip, changed=None):
        """Stamped Smart Objects (sculpt, eye brilliance, soft-focus glow) are snapshots of what was below them:
        give each, bottom to top, what is below it now; with `changed` (layer ids), only those with one of these
        below them, each then changed too for the ones above it. Their smart filters re-run on the new contents."""
        top = self.doc.tree()
        order = [n.id for n in self.doc.flat(top)]  # drawing order: a folder comes before what is in it
        for node in self.stamped(top):
            if node.id in skip:
                continue
            if changed is not None:
                folders, parent = set(), node.parent  # the ones it is in come before it but aren't below it
                while parent is not None:
                    folders.add(parent.id)
                    parent = parent.parent
                if not (set(order[:order.index(node.id)]) - folders) & changed:
                    continue  # its snapshot is still what is below it
                changed.add(node.id)
            with self.doc.only_below(node.id, include_self=False):
                base = os.path.join(self.staging, f"stamp-{node.id}.png")
                self.doc.export(base)
            self.doc.s.call("doc.replaceSmartObject", {"path": base, "layer": node.id})
            self.redone.append((node.id, "按现在的画面重新盖印"))
            self.changed = True

    def update(self):
        args = self.args
        steps = (["refresh"] if args.refresh else []) + args.modules
        if args.refresh:
            self.step(f"1/{len(steps)} refresh")
            self.refresh()
        before, redone = {n.id for n in self.stamped()}, len(self.redone)
        for number, key in enumerate(args.modules, len(steps) - len(args.modules) + 1):
            self.step(f"{number}/{len(steps)} {key}")
            self.add(key)
        if self.added and before:  # the stamps with a layer added or made again below them
            restamped = len(self.redone)
            self.restamp({n for n in self.added} | {i for i, _ in self.redone if i not in before},
                         changed=set(self.added) | {i for i, _ in self.redone[redone:]})
            if len(self.redone) == restamped:
                say("[NOTE] 骨相、眼神光、梦幻柔焦的盖印没有重做（新图层都在它们上面）；在 PhotoCraft 里改过它们下面的图层的话，"
                    "跑一次 --refresh")


def describe(node):
    kind = {"Group": "folder", "Pixel": "pixels", "Adjustment": "adjustment"}.get(node.kind, node.kind or "?")
    if node.is_smart:
        kind = "Smart Object" + (" · " + ", ".join(f.split(".")[-1] for f in node.filters) if node.filters else "")
    raw = node.raw
    bits = [kind, str(raw.get("blend", "Normal")), f"{round(float(raw.get('opacity', 1)) * 100)}%"]
    if raw.get("hasMask"):
        bits.append("mask")
    if not node.visible:
        bits.append("hidden")
    return f"{node.name}  [{', '.join(bits)}]"


def stack(doc):
    lines = []

    def visit(nodes, depth):
        for node in reversed(nodes):
            lines.append("  " * (depth + 1) + describe(node))
            visit(node.children, depth + 1)
    top = doc.tree()
    visit(top, 0)
    return top, lines


def same_file(stat, path):
    try:
        now = os.stat(path)
    except OSError:
        return False
    return (now.st_mtime_ns, now.st_size) == (stat.st_mtime_ns, stat.st_size)


def clone(source, target):
    """APFS clone (instant, no extra space) when it can, a copy otherwise."""
    if subprocess.run(["cp", "-c", source, target], capture_output=True).returncode != 0:
        shutil.copy2(source, target)


@contextmanager
def held_stops():
    """A Stop (SIGINT: Compositor 工具's stop button, ^C) held off for the block, and noted: Python runs a handler in
    the main thread only, between two steps, where a blocked signal would still reach the serve reader threads and
    stop the main thread at any step. What came, for the caller to act on."""
    stops = []
    before = signal.signal(signal.SIGINT, lambda *_: stops.append(True))
    try:
        yield stops
    finally:
        signal.signal(signal.SIGINT, before)


def staging_name(dest):
    """The name the scratch copies of `dest` are saved under: its own, as a document keeps the name it was saved as
    and shows it, changed only as far as the engine needs (see photocraft.refused_name)."""
    name = os.path.basename(dest)
    if refused_name(name):
        name = name.replace(":", "-").replace("\\", "-")
        if refused_name(name):  # a device name: con.pcraft
            stem, _, rest = name.partition(".")
            name = f"{stem}_.{rest}"
    return "document.pcraft" if refused_name(name) else name


def file_stamp(path):
    try:
        s = os.stat(path)
    except OSError:
        return None
    return [s.st_dev, s.st_ino, s.st_mtime_ns, s.st_size]


def stale_tabs():
    """{path: stamp} of the documents a run wrote while the linked PhotoCraft had a tab of them open that it couldn't
    load again (mark_stale), left as that run wrote them: the tab is older than the file. One saved since, from
    PhotoCraft or by another run, is done with."""
    try:
        with open(STALE_FILE, encoding="utf-8") as f:
            marks = json.load(f)
    except (OSError, ValueError):
        return {}
    return {path: mark for path, mark in marks.items() if file_stamp(path) == mark} if isinstance(marks, dict) else {}


def mark_stale(dest):
    marks = dict(stale_tabs(), **{os.path.realpath(dest): file_stamp(dest)})
    try:
        os.makedirs(os.path.dirname(STALE_FILE), exist_ok=True)
        with open(STALE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(marks, f, ensure_ascii=False)
        os.replace(STALE_FILE + ".tmp", STALE_FILE)
    except OSError as error:
        warn(f"couldn't note that PhotoCraft's tab of {os.path.basename(dest)} is older than the file ({error}): "
             "don't save it there (⌘S) before loading it again (F12)")


def open_tab(app, path, name, then, contents):
    """The tab the linked PhotoCraft has of the file `path` ({index, name, dirty, …}), or None. The run stops (`then`
    says what it leaves) when the app can't say, when the document is open in more than one tab, and, with
    `contents`, when a smart object's contents are open in a tab of their own: loading the document again would cut
    them loose from it."""
    try:
        session = app.session()
        tabs = app.tabs(path, session)
        opened = app.contents_tabs(session) if tabs and contents else []
    except ServeError as error:
        raise Fail(f"PhotoCraft (linked) didn't list its open documents ({error}): {then}close any dialog open there, "
                   "or wake the display (it answers while it draws), and run again") from error
    if len(tabs) > 1:
        raise Fail(f"PhotoCraft has {name} open in {len(tabs)} tabs: {then}close all but one there (the one furthest "
                   "right is the newest) and run again")
    if opened:
        raise Fail(f"PhotoCraft has a smart object's contents (of this document or another) open in a tab of their "
                   f"own ({', '.join(doc.get('name') or '?' for doc in opened)}): {then}close that tab there (its "
                   "changes go back into their document) and run again")
    return tabs[0] if tabs else None


def linked_app(dest, args):
    """Before the run, when the PhotoCraft app is linked (see photocraft.connect_app) and has the file `dest` open:
    its unsaved changes there are saved first, so this run starts from them and loading the result again loses
    nothing. Unless that tab is older than the file (stale_tabs): saving it would put back what the file had before
    the run that wrote it. Whether the app has a tab of it."""
    if not os.path.isfile(dest) or not (args.modules or args.refresh or args.init):
        return False
    app, why = connect_app()
    if app is None:
        if why:
            say(f"[NOTE] PhotoCraft 联动没连上（{why}），照旧手动载入")
        return False
    name = os.path.basename(dest)
    try:
        outside = outside_root(dest, app.root)
        doc = open_tab(app, dest, name, "", contents=not outside)
        if doc is None:
            return False
        if not doc.get("dirty"):
            if outside:
                say(f"[NOTE] PhotoCraft 联动只管它能读写的文件夹里的文档（{outside}）：{name} 做完照旧在 PhotoCraft 里按 F12 载入")
            return True
        if file_stamp(dest) in stale_tabs().values():
            raise Fail(f"PhotoCraft's {name} has unsaved changes but may be older than the file (a run wrote it "
                       "while it was open there and couldn't load it again): saving it would undo that run. Load it in "
                       "PhotoCraft (F12), make the changes again and run again. If it was loaded since (F12, or "
                       "opened again), save it there (⌘S) and run again")
        if outside:
            raise Fail(f"PhotoCraft has {name} open with unsaved changes, and the link only saves documents in the "
                       f"folder it was given ({outside}): save it there (⌘S) and run again")
        if args.init:
            raise Fail(f"PhotoCraft has {name} open with unsaved changes, and --init --force replaces the document: "
                       "save or close it there first")
        try:
            app.save(doc)
        except ServeError as error:
            raise Fail(f"PhotoCraft has {name} open and it could not be saved there ({error}): save it (⌘S) and "
                       "run again") from error
        say(f"[OK] PhotoCraft: saved {doc.get('name') or name} first, with its unsaved changes")
        return True
    finally:
        app.close()


def app_before_write(dest, init, seen):
    """Just before the new document replaces the file `dest`: the linked PhotoCraft app when it has a tab of it to
    load it again in (reload_in_app), else None; and whether it has a tab of it. Changes made in that tab while this
    ran stop the run, nothing written, as the file's being saved does; so does an app that had it open (`seen`) and
    can't be asked again (one that has quit has no tabs left)."""
    name = os.path.basename(dest)
    app, why = connect_app()  # a new connection: the app drops one idle for 30 s, and building takes longer
    if app is None:
        if seen and why:
            raise Fail(f"PhotoCraft had {name} open and the link couldn't reach it again ({why}): nothing written. "
                       "Run it again")
        return None, False
    try:
        outside = outside_root(dest, app.root)
        doc = open_tab(app, dest, name, "nothing written. ", contents=not outside)
        if doc and doc.get("dirty"):
            raise Fail(f"{name} was changed in PhotoCraft while this ran: nothing written. "
                       + ("Save or close it there and run it again" if init else "Run it again: those changes are "
                          "saved there first"))
    except BaseException:
        app.close()
        raise
    if doc is None or outside:
        app.close()
        return None, doc is not None
    return app, True


def reload_in_app(app, dest):
    """Load the new document again in the PhotoCraft tab app_before_write found: True when it did, False when it
    couldn't (the tab is older than the file now: see mark_stale), None when there was no tab."""
    if app is None:
        return None
    try:
        if not app.reload(dest):
            return None
        say(f"[OK] PhotoCraft: {os.path.basename(dest)} loaded again with the new layers (its History starts over)")
        return True
    except ServeError as error:
        warn(f"PhotoCraft didn't load {os.path.basename(dest)} again ({error})")
        return False
    finally:
        app.close()


def main(argv):
    started = time.time()
    RB = load_builder()
    args = parse_args(argv, RB)
    given = os.path.abspath(args.pcraft)  # where the render and check images go, beside what the caller named
    dest = os.path.realpath(given)  # a link's own file: what PhotoCraft has open, and what is written
    if not dest.endswith(".pcraft"):
        raise Fail("--pcraft must name a .pcraft file")
    parent = os.path.dirname(dest)
    if not os.path.isdir(parent):
        raise Fail(f"no folder {parent}")
    if os.path.isdir(dest):  # PhotoCraft saves .pcraft as a ZIP; folder bundles are its autosave recovery format
        raise Fail(f"{dest} is a folder bundle (PhotoCraft's recovery format): open it in PhotoCraft and save it as a "
                   ".pcraft file first")
    if args.init:
        if not os.path.isfile(args.init):
            raise Fail(f"cannot read the photo {args.init}")
        if os.path.exists(dest) and not args.force:
            raise Fail(f"{dest} exists: add --force to replace it (its layers are lost), or leave out --init to add to it")
    elif not os.path.isfile(dest):
        raise Fail(f"no document {dest}: create it with --init <photo>")
    if not (args.init or args.modules or args.refresh or args.render is not None):
        say("[NOTE] nothing to add: listing the document's layers")

    cli = find_cli(say, warn)
    seen = linked_app(dest, args)  # PhotoCraft (linked) has it open
    staging = tempfile.mkdtemp(prefix=".pcraft-retouch-", dir=parent)  # the serve session's only folder
    serve = Serve(cli, staging)
    run = Run(args, RB, given, staging, serve)
    app, there, written, loaded = None, False, False, None
    try:
        if args.init:
            run.create()
        else:
            opened_as = os.stat(dest)
            working = os.path.join(staging, "in", staging_name(dest))
            os.mkdir(os.path.dirname(working))
            clone(dest, working)
            serve.call("doc.open", {"path": working})
            run.update()
        if run.changed:
            out = os.path.join(staging, staging_name(dest))
            serve.call("doc.save", {"path": out})
            if not args.init and not same_file(opened_as, dest):
                raise Fail(f"{dest} was saved (in PhotoCraft?) while this ran: nothing written. Run it again")
            app, there = app_before_write(dest, args.init, seen)
            with held_stops() as stops:  # written or not, so what is said after is true
                os.replace(out, dest)
                written = True
            if stops:
                raise KeyboardInterrupt
            loaded = reload_in_app(app, dest)
        top, lines = stack(run.doc)
        nodes = {n.id: n for n in Doc.flat(top)}
        for layer_id, how in run.redone:
            if layer_id in nodes:
                say(f"[OK] ~ {nodes[layer_id].name} ({how})")
        shown = set()
        for layer_id in run.added:
            node = nodes.get(layer_id)
            if node and layer_id not in shown and (node.parent is None or node.parent.id not in run.added):
                shown.add(layer_id)
                say(f"[OK] + {describe(node)}")
        if args.render is not None:
            run.step("render")
            target = os.path.abspath(args.render or os.path.splitext(given)[0] + ".render.jpg")
            flat = os.path.join(staging, "render.png")
            run.doc.export(flat)
            image = Image.open(flat)
            if target.lower().endswith((".jpg", ".jpeg")):
                image.convert("RGB").save(target, quality=92)
            else:
                shutil.copy2(flat, target)
            say(f"[OK] Rendered {target} ({image.width}x{image.height}, by PhotoCraft)")
        checks = getattr(run, "checks", {})
        for name in sorted(checks, key=lambda n: (n != "overview.jpg", n)):
            say(f"[OK] Check image: {checks[name]}")
        count = len(Doc.flat(top))
        say(f"[SUCCESS] {dest}: {count} layers, {time.time() - started:.1f}s" + ("" if run.changed else " (unchanged)"))
        say("Stack, top to bottom:")
        for line in lines:
            say(line)
        if run.changed and not loaded and (there or not args.init):
            say("[NOTE] 已在 PhotoCraft 里打开这个文档的话，按 F12（文件 › 恢复）载入新版本；先不要按 ⌘S，否则会盖掉这次的修改")
    except BaseException as error:
        if not written or isinstance(error, SystemExit):
            raise
        why = ("interrupted" if isinstance(error, KeyboardInterrupt) else f"{error}" if isinstance(error, (Fail, ServeError))
               else f"{type(error).__name__}: {error}")
        then = "PhotoCraft has loaded it again" if loaded else "if PhotoCraft has it open, F12 there loads it"
        raise Fail(f"{why}, after {os.path.basename(dest)} was written with the new layers ({then})") from error
    finally:
        with held_stops():  # the clearing up runs to its end: a Stop now finds the run over
            if app:
                app.close()
            if written and there and loaded is not True:  # PhotoCraft's tab of it is older than the file now
                mark_stale(dest)
            serve.close()
            keep = os.environ.get("PCRAFT_RETOUCH_KEEP")  # tests: keep the builder's scratch projects to compare with
            if keep:
                for name in os.listdir(staging):
                    if name.endswith(".comp"):
                        shutil.copytree(os.path.join(staging, name), os.path.join(keep, name), dirs_exist_ok=True)
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (Fail, ServeError) as error:
        say(f"[Error] {error}")
        sys.exit(1)
    except KeyboardInterrupt:
        say("[Error] interrupted: the document is unchanged")
        sys.exit(1)
    except Exception as error:  # noqa: BLE001 — keep the [Error] contract; main says when it stopped after writing
        say(f"[Error] {type(error).__name__}: {error} (the document is unchanged)")
        sys.exit(1)
