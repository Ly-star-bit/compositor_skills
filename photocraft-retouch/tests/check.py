"""Checks for pcraft_retouch.py: each suite builds documents with the skill, then compares what PhotoCraft draws
with what the compositor-retouch builder draws for the same layers (its --render, the Compositor reference).
Command line only: photocraft-cli serve has no window. Usage: check.py WORKDIR PHOTO SUITE..."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

TESTS = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(TESTS), "scripts")
sys.path.insert(0, SCRIPTS)
from photocraft import Serve, find_cli  # noqa: E402

SKILL_SCRIPT = os.path.join(SCRIPTS, "pcraft_retouch.py")
BUILDER = os.environ.get("RETOUCH_BUILDER") or os.path.join(os.path.dirname(os.path.dirname(TESTS)), "scripts",
                                                             "retouch_builder.py")
sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location("retouch_builder", BUILDER)
RB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RB)

failures = []


def ok(condition, label):
    print(("  ok: " if condition else "  BAD: ") + label, flush=True)
    if not condition:
        failures.append(label)


def skill(work, *argv, keep=None, expect=0):
    env = dict(os.environ)
    if keep:
        os.makedirs(keep, exist_ok=True)
        env["PCRAFT_RETOUCH_KEEP"] = keep
    r = subprocess.run([sys.executable, SKILL_SCRIPT, *argv], capture_output=True, text=True, cwd=work, env=env)
    if r.returncode != expect:
        print(r.stdout[-2000:], r.stderr[-2000:])
    ok(r.returncode == expect, f"pcraft_retouch {' '.join(a for a in argv if not a.startswith('/'))[:90]} exits {expect}")
    return r.stdout


def builder_render(comp, out, hide_kinds=(), show_folders=()):
    """The builder's own render of a copy of `comp` with some layers hidden or folders shown."""
    copy = out + ".comp"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(comp, copy)
    path = os.path.join(copy, "manifest.json")
    m = json.load(open(path))
    for layer in m["layers"]:
        if (layer.get("adjustment") or {}).get("kind") in hide_kinds:
            layer["isVisible"] = False
        if layer.get("isGroup") and layer["name"].startswith(tuple(show_folders)):
            layer["isVisible"] = True
    json.dump(m, open(path, "w"), ensure_ascii=False)
    r = subprocess.run([sys.executable, BUILDER, "--comp", copy, "--render", out], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return rgb(out)


def rgb(path):
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


class Pc:
    """Open a .pcraft headless, set eyes, flatten."""

    def __init__(self, work):
        self.work = work
        self.s = Serve(find_cli(print, print), work)

    def open(self, pcraft):
        self.s.call("doc.open", {"path": pcraft})

    def nodes(self):
        d = self.s.call("doc.inspect")
        d = d.get("document", d)
        out = []

        def walk(layers, parent):
            for layer in layers:
                out.append((layer, parent))
                walk(layer.get("children") or layer.get("layers") or [], layer)
        walk(d["layers"], None)
        return out

    def render(self, out, hide=(), show=()):
        """hide: name prefixes or smart-filter commands to hide; show: folder name prefixes to show."""
        changed = []
        for layer, _ in self.nodes():
            filters = {f.get("command") for f in layer.get("smartFilters") or []}
            want = layer.get("visible", True)
            if layer["name"].startswith(tuple(hide)) or filters & set(hide):
                want = False
            if layer["name"].startswith(tuple(show)):
                want = True
            if want != layer.get("visible", True):
                self.s.run("layer.setProps", {"layer": layer["id"], "visible": want})
                changed.append((layer["id"], layer.get("visible", True)))
        if os.path.exists(out):
            os.remove(out)
        self.s.call("doc.save", {"path": out})
        for layer_id, visible in changed:
            self.s.run("layer.setProps", {"layer": layer_id, "visible": visible})
        return rgb(out)

    def close(self):
        try:
            self.s.call("doc.close", {})
        except Exception:
            pass

    def stop(self):
        self.s.close()


def diff(a, b, label, max_limit, mean_limit):
    d = np.abs(a - b)
    ok(d.max() <= max_limit and d.mean() <= mean_limit,
       f"{label}: max {d.max()} (≤ {max_limit}), mean {d.mean():.3f} (≤ {mean_limit})")


def effect(label, mine, theirs, region, corr_limit, ratio_range):
    a, t = mine[region].ravel(), theirs[region].ravel()
    corr = float(np.corrcoef(a, t)[0, 1])
    ratio = float(np.abs(a).mean() / max(1e-6, np.abs(t).mean()))
    ok(corr >= corr_limit and ratio_range[0] <= ratio <= ratio_range[1],
       f"{label}: correlation {corr:.3f} (≥ {corr_limit}), strength ratio {ratio:.2f} (in {ratio_range})")


LIVE = ("Tonal Contrast", "Sharpen", "Grain")
LIVE_FILTERS = ("filter.other.highPass", "filter.sharpen.unsharpMask", "filter.noise.addNoise")


def suite_dr5(work, photo):
    print("== dr5: a new document, layer by layer against the builder")
    keep = os.path.join(work, "keep-dr5")
    skill(work, "--init", photo, "--pcraft", os.path.join(work, "dr5.pcraft"), "--preset", "dr5", keep=keep)
    comp = os.path.join(keep, "01-init.comp")
    pc = Pc(work)
    try:
        pc.open(os.path.join(work, "dr5.pcraft"))
        names = [layer["name"] for layer, _ in pc.nodes()]
        ok(len(names) == len(json.load(open(os.path.join(comp, "manifest.json")))["layers"]),
           f"one PhotoCraft layer per builder layer ({len(names)})")
        base = pc.render(os.path.join(work, "pc_px.png"), hide=LIVE_FILTERS)
        ref = builder_render(comp, os.path.join(work, "rb_px.png"), hide_kinds=LIVE)
        diff(base, ref, "photo, frequency separation, smoothing, dual curves", 1, 0.01)
        for tag, folder, limits in (("bw", "🔍 观察组 · 黑白", (2, 0.2)), ("color", "🔍 观察组 · 颜色", (6, 0.4))):
            diff(pc.render(os.path.join(work, f"pc_{tag}.png"), hide=LIVE_FILTERS, show=(folder,)),
                 builder_render(comp, os.path.join(work, f"rb_{tag}.png"), hide_kinds=LIVE, show_folders=(folder,)),
                 f"observation folder {tag} (curves, B&W, Luminosity)", *limits)
        for kind, command, corr, ratio in (("Tonal Contrast", "filter.other.highPass", 0.8, (0.7, 1.4)),
                                           ("Sharpen", "filter.sharpen.unsharpMask", 0.95, (0.8, 1.25))):
            mine = pc.render(os.path.join(work, "pc_one.png"), hide=tuple(c for c in LIVE_FILTERS if c != command)) - base
            theirs = builder_render(comp, os.path.join(work, "rb_one.png"), hide_kinds=tuple(k for k in LIVE if k != kind)) - ref
            record = next(r for r in json.load(open(os.path.join(comp, "manifest.json")))["layers"]
                          if (r.get("adjustment") or {}).get("kind") == kind)
            region = np.asarray(Image.open(os.path.join(comp, "images", record["maskFile"])).convert("L")) > 0
            effect(f"{kind} as a stamped Smart Object", mine, theirs, region, corr, ratio)
        mine = pc.render(os.path.join(work, "pc_grain.png"), hide=LIVE_FILTERS[:2]) - base
        theirs = builder_render(comp, os.path.join(work, "rb_grain.png"), hide_kinds=LIVE[:2]) - ref
        ratio = mine.std() / theirs.std()
        ok(0.75 <= ratio <= 1.33, f"grain strength: PhotoCraft std {mine.std():.2f} vs builder {theirs.std():.2f}")
        pc.close()
    finally:
        pc.stop()


def suite_looks(work, photo):
    print("== looks: each grade at strength 0.7, against the builder")
    pc = Pc(work)
    try:
        for look in sorted(RB.LOOKS):
            keep = os.path.join(work, f"keep-{look}")
            target = os.path.join(work, f"look-{look}.pcraft")
            skill(work, "--init", photo, "--pcraft", target, "--look", look, "--strength", "0.7", "--no-checks", keep=keep)
            pc.open(target)
            diff(pc.render(os.path.join(work, "pc_look.png")),
                 builder_render(os.path.join(keep, "01-init.comp"), os.path.join(work, "rb_look.png")),
                 f"look {look}", 8, 0.5)
            pc.close()
    finally:
        pc.stop()


def suite_glow(work, photo):
    print("== glow: styles and colours, against the builder")
    pc = Pc(work)
    try:
        for spec in ("mist", "mist:gold", "halation", "mist:gold,rose", "dream"):
            keep = os.path.join(work, f"keep-glow-{spec.replace(':', '_').replace(',', '_')}")
            target = os.path.join(work, f"glow-{spec.replace(':', '_').replace(',', '_')}.pcraft")
            skill(work, "--init", photo, "--pcraft", target, "--glow", spec, "--no-checks", keep=keep)
            pc.open(target)
            limits = (12, 1.0) if spec == "dream" else (4, 0.3)
            diff(pc.render(os.path.join(work, "pc_glow.png")),
                 builder_render(os.path.join(keep, "01-init.comp"), os.path.join(work, "rb_glow.png")),
                 f"glow {spec}", *limits)
            pc.close()
    finally:
        pc.stop()


def suite_append(work, photo):
    print("== append: studio, then a look and a glow added to the saved document, then everything again")
    target = os.path.join(work, "append.pcraft")
    skill(work, "--init", photo, "--pcraft", target, "--preset", "studio", "--no-checks")
    out = skill(work, "--pcraft", target, "--look", "film", "--glow", "mist:gold", "--no-checks")
    ok("[NOTE]" in out and "F12" in out, "an added-to document tells the user to press F12 in PhotoCraft")
    # the builder doing the same on its own project
    comp = os.path.join(work, "append-ref.comp")
    shutil.rmtree(comp, ignore_errors=True)
    for argv in (["--init", photo, "--preset", "studio"], ["--look", "film", "--glow", "mist:gold"]):
        r = subprocess.run([sys.executable, BUILDER, "--comp", comp, "--no-checks", *argv], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    pc = Pc(work)
    try:
        pc.open(target)
        top = [layer["name"].split(" ")[0] for layer, parent in pc.nodes() if parent is None]
        m = json.load(open(os.path.join(comp, "manifest.json")))
        ref_top = [r["name"].split(" ")[0] for r, _ in reversed([(r, d) for r, d in RB.render_order(m["layers"]) if d == 0])]
        ok(top == ref_top, f"top-level order as the builder's: {' '.join(top)}")
        diff(pc.render(os.path.join(work, "pc_append.png")), builder_render(comp, os.path.join(work, "rb_append.png")),
             "studio + film + gold mist, added afterwards", 8, 0.5)
        count = len(pc.nodes())
        pc.close()
        out = skill(work, "--pcraft", target, "--preset", "daily", "--look", "film", "--no-checks")
        ok("[SKIP] --look film" in out and "[SKIP] --neutral-gray" in out and "[SKIP] --inspection" in out,
           "a second run skips the look, neutral gray and observation folders")
        pc.open(target)
        names = [layer["name"] for layer, _ in pc.nodes()]
        ok(len(names) == count + 2 and sum(n.startswith("🔥") for n in names) == 1
           and sum(n.startswith("🧴") for n in names) == 1,
           f"…and adds only the smoothing in its folder ({count} → {len(names)} layers)")
        pc.close()
        out = skill(work, "--pcraft", target, "--delicious-skin", "--skin-strength", "0.6", "--no-checks")
        ok("重做 · 保留蒙版" in out, "running the smoothing again redoes it in place")
        pc.open(target)
        skin = [layer for layer, _ in pc.nodes() if layer["name"].startswith("🔥")]
        ok(len(skin) == 1 and abs(skin[0]["opacity"] - 0.53) < 0.01 and "力度 0.6" in skin[0]["name"],
           f"…at the new strength: {round(skin[0]['opacity'] * 100) if skin else '?'}%")
        pc.close()
    finally:
        pc.stop()


def suite_refresh(work, photo):
    print("== refresh: work on the high-frequency layer, save, recompute")
    target = os.path.join(work, "refresh.pcraft")
    skill(work, "--init", photo, "--pcraft", target, "--preset", "dr5", "--no-checks")
    pc = Pc(work)
    try:
        pc.open(target)
        nodes = pc.nodes()
        high = next(layer for layer, _ in nodes if layer["name"].startswith("高频"))
        skin_before = next(layer for layer, _ in nodes if layer["name"].startswith("🔥"))
        d = pc.s.call("doc.inspect")
        d = d.get("document", d)
        w, h = d["width"], d["height"]
        x0, y0, x1, y1 = int(w * 0.38), int(h * 0.30), int(w * 0.62), int(h * 0.42)  # across the face
        pc.s.run("layer.select", {"layer": high["id"]})
        try:
            pc.s.run("select.rect", {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})
            pc.s.run("edit.fill", {"contents": "color", "color": "#808080"})  # Linear Light's neutral: no texture
            pc.s.run("select.deselect")
            filled = True
        except Exception as error:  # noqa: BLE001
            print("   fill failed:", error)
            filled = None
        before = pc.render(os.path.join(work, "pc_before_refresh.png"), hide=LIVE_FILTERS)
        saved = os.path.join(work, "refresh.pcraft")
        os.remove(saved)
        pc.s.call("doc.save", {"path": saved})
        pc.close()
        ok(filled is not None, "texture removed from the high-frequency layer (filled with neutral grey) and saved")
        out = skill(work, "--pcraft", target, "--refresh", "--no-checks")
        ok("按现在的画面重算" in out and "重新盖印" in out, "refresh recomputes the smoothing and re-stamps sculpt and eyes")
        pc.open(target)
        nodes = pc.nodes()
        skin_after = next(layer for layer, _ in nodes if layer["name"].startswith("🔥"))
        ok((skin_after["id"], skin_after["name"], skin_after["opacity"], skin_after["hasMask"]) ==
           (skin_before["id"], skin_before["name"], skin_before["opacity"], skin_before["hasMask"]),
           "the smoothing keeps its id, name, opacity and mask")
        after = pc.render(os.path.join(work, "pc_after_refresh.png"), hide=LIVE_FILTERS)
        # with the texture gone below, the recomputed smoothing no longer carries the old texture on top
        def lap(img):
            img = img.mean(axis=2)[y0:y1, x0:x1]
            return np.abs(img[1:-1, 1:-1] * 4 - img[:-2, 1:-1] - img[2:, 1:-1] - img[1:-1, :-2] - img[1:-1, 2:]).mean()
        b, a = lap(before), lap(after)
        ok(a < b * 0.8, f"fine texture after refresh {a:.2f} < before {b:.2f} (the old snapshot hid the edit)")
        pc.close()
    finally:
        pc.stop()


def suite_errors(work, photo):
    print("== errors and safety")
    target = os.path.join(work, "keep.pcraft")
    skill(work, "--init", photo, "--pcraft", target, "--neutral-gray", "--no-checks")
    stamp = os.stat(target).st_mtime_ns
    skill(work, "--init", photo, "--pcraft", target, "--grain", expect=1)
    ok(os.stat(target).st_mtime_ns == stamp, "--init on an existing document without --force leaves it alone")
    skill(work, "--pcraft", target, "--preset", "dr5", expect=1)
    skill(work, "--pcraft", os.path.join(work, "missing.pcraft"), "--grain", expect=1)
    skill(work, "--init", photo, "--pcraft", target, "--grain", "--force", "--no-checks")
    pc = Pc(work)
    try:
        pc.open(target)
        ok(not any(layer["name"].startswith("🩶") for layer, _ in pc.nodes()), "--force replaces the document")
        pc.close()
    finally:
        pc.stop()
    leftovers = [n for n in os.listdir(work) if n.startswith(".pcraft-retouch-")]
    ok(not leftovers, "no scratch folders left behind")


SUITES = {"dr5": suite_dr5, "looks": suite_looks, "glow": suite_glow, "append": suite_append,
          "refresh": suite_refresh, "errors": suite_errors}

if __name__ == "__main__":
    import tempfile
    work = tempfile.mkdtemp(prefix="run-", dir=sys.argv[1])  # a fresh folder each time
    photo = os.path.abspath(sys.argv[2])
    print(f"work folder: {work}")
    for name in sys.argv[3:] or list(SUITES):
        SUITES[name](work, photo)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
