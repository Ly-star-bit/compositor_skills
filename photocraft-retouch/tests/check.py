"""Checks for pcraft_retouch.py: each suite builds documents with the skill, then compares what PhotoCraft draws
with what the compositor-retouch builder draws for the same layers (its --render, the Compositor reference).
Command line only: photocraft-cli serve has no window. Usage: check.py WORKDIR PHOTO SUITE..."""
import importlib.util
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading

import numpy as np
from PIL import Image

TESTS = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(TESTS), "scripts")
sys.path.insert(0, SCRIPTS)
from photocraft import Serve, ServeError, find_cli, outside_root  # noqa: E402

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


def isolated(work):
    """The script's environment in tests: never this Mac's own PhotoCraft link (a closed port instead of the link
    file), its stale marks in the work folder."""
    return dict(os.environ, PHOTOCRAFT_CONTROL_PORT="1", PCRAFT_RETOUCH_STALE=os.path.join(work, "stale.json"))


def skill(work, *argv, keep=None, expect=0, env=None):
    env = dict(isolated(work), **(env or {}))
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


def interior_blending(psd):
    """Layers saved with Blending Options › Blend Interior Effects as Group (PSD 'infx', written only when on):
    Advanced Blending, which PhotoCraft composites on the CPU for the whole document."""
    data = open(psd, "rb").read()
    flags, i = [], data.find(b"8BIMinfx")
    while i >= 0:
        flags.append(data[i + 12])
        i = data.find(b"8BIMinfx", i + 12)
    return sum(1 for f in flags if f)


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
            psd = os.path.join(work, "glow.psd")
            if os.path.exists(psd):
                os.remove(psd)
            pc.s.call("doc.save", {"path": psd})
            ok(interior_blending(psd) == 0, f"glow {spec}: no Advanced Blending, so PhotoCraft keeps the GPU compositor")
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
        ok(any(n.startswith(RB.MANUAL_PREFIX) for n in names), "…brushed on by hand, the default")
        pc.close()
        out = skill(work, "--pcraft", target, "--freq-sep", "--blur-radius", "8", "--no-checks")
        ok("[SKIP] --freq-sep" in out and "isn't made again" in out,
           "another radius on the document's pair: skipped, saying PhotoCraft's pair isn't made again")
        out = skill(work, "--pcraft", target, "--delicious-skin", "--skin-strength", "0.6", "--no-checks")
        ok("重做 · 保留蒙版" in out, "running the smoothing again redoes it in place")
        pc.open(target)
        skin = [layer for layer, _ in pc.nodes() if layer["name"].startswith("🔥")]
        ok(len(skin) == 1 and abs(skin[0]["opacity"] - 0.53) < 0.01 and "力度 0.6" in skin[0]["name"],
           f"…at the new strength: {round(skin[0]['opacity'] * 100) if skin else '?'}%")
        pc.close()
        skill(work, "--pcraft", target, "--sculpt", "--no-checks")
        out = skill(work, "--pcraft", target, "--glow", "halation", "--no-checks")
        ok("重新盖印" not in out, "a glow added above the sculpt's stamp leaves the stamp as it is")
        out = skill(work, "--pcraft", target, "--delicious-skin", "auto", "--no-checks")
        ok(any(line.startswith("[OK] ~ 🗿") and "重新盖印" in line for line in out.splitlines()),
           "a smoothing added below it: the sculpt is stamped again")
    finally:
        pc.stop()


def suite_refresh(work, photo):
    print("== refresh: work on the high-frequency layer, save, recompute")
    target = os.path.join(work, "refresh.pcraft")
    # the automatic mask: a snapshot that covers the face, so it hides the edit until recomputed
    skill(work, "--init", photo, "--pcraft", target, "--preset", "dr5", "--delicious-skin", "auto", "--no-checks")
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
    for name in ("con.pcraft", "a:b.pcraft"):  # names the engine refuses (a `:` is Finder's `/`)
        odd = os.path.join(work, name)
        skill(work, "--init", photo, "--pcraft", odd, "--neutral-gray", "--no-checks")
        skill(work, "--pcraft", odd, "--grain", "--no-checks")
    print("  stopped after the document is written (during --render):")
    target = os.path.join(work, "stopped.pcraft")
    skill(work, "--init", photo, "--pcraft", target, "--no-checks")
    stamp = os.stat(target).st_mtime_ns
    proc = subprocess.Popen([sys.executable, SKILL_SCRIPT, "--pcraft", target, "--grain", "--render", "--no-checks"],
                            stdout=subprocess.PIPE, text=True, cwd=work, env=isolated(work))
    out = []
    for line in proc.stdout:
        out.append(line)
        if line.startswith("[OK] +"):  # written; the render comes next
            proc.send_signal(signal.SIGINT)
    proc.wait()
    error = next((line for line in out if line.startswith("[Error]")), "")
    ok(proc.returncode == 1 and os.stat(target).st_mtime_ns != stamp and "was written with the new layers" in error
       and "unchanged" not in error, f"...says the document was written: {error.strip()[:120]}")
    leftovers = [n for n in os.listdir(work) if n.startswith(".pcraft-retouch-")]
    ok(not leftovers, "no scratch folders left behind")


class FakeApp:
    """The PhotoCraft app's control port as the protocol has it (an `auth` frame first, then JSON lines), its document
    work done by a real headless engine: app.open and app.save become serve's doc.open and doc.save (which, like the
    app's, writes a .pcraft back to its own file without a path), the rest goes through as it is. Like the app it
    drops a connection idle for `idle` seconds (30 there). What the user or the app does meanwhile: `refuse`, calls
    (methods, or the commands of engine.execute) answered with an error; `before`, what happens before a call (once);
    `hooks`, what happens as the nth connection comes in; `drop`, connections turned away at once, as the app does
    past its limit."""

    def __init__(self, work, idle=None):
        self.work, self.idle = work, idle
        self.serve, self.lock = Serve(find_cli(print, print), work), threading.Lock()
        self.token, self.calls = secrets.token_hex(32), []
        self.refuse, self.before, self.hooks, self.drop, self.connections = set(), {}, {}, set(), 0
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        self.connections += 1
        number = self.connections
        if number in self.drop:
            connection.sendall(b'{"id":null,"ok":false,"error":"connection limit reached"}\n')
            connection.close()
            return
        connection.settimeout(self.idle)
        lines, authed = connection.makefile("r", encoding="utf-8"), False
        try:
            for line in lines:
                request = json.loads(line)
                method, params = request.get("method"), request.get("params") or {}
                name = method if method != "engine.execute" else params.get("command")
                if not authed:
                    authed = method == "auth" and params.get("token") == self.token
                    reply = {"ok": True, "result": {}} if authed else {"ok": False, "error": "unauthorized"}
                    if authed:
                        self.hooks.pop(number, lambda: None)()
                elif name in self.refuse:
                    self.calls.append(name)
                    reply = {"ok": False, "error": "command is unavailable while a modal dialog is open"
                             if method == "engine.execute" else "automation path rejected: absolute paths must be "
                                                                "inside the automation root"}
                else:
                    self.calls.append(name)
                    self.before.pop(name, lambda: None)()
                    try:
                        reply = {"ok": True, "result": self.call({"app.open": "doc.open", "app.save": "doc.save"}.get(method, method), params)}
                    except ServeError as error:
                        reply = {"ok": False, "error": str(error)}
                connection.sendall((json.dumps(dict(reply, id=request.get("id"))) + "\n").encode())
                if not authed:
                    break
        except OSError:  # idle too long (socket.timeout), or the client went away
            pass
        connection.close()

    def call(self, method, params=None):
        with self.lock:
            return self.serve.call(method, params)

    def run(self, command, params=None):
        return self.call("engine.execute", {"command": command, "params": params or {}, "wait": True})

    def documents(self):
        return self.call("session.list")["documents"]

    def edit(self, index, name):
        """The user adds a layer to the document at `index` (unsaved), the one in front in front again after."""
        active = self.call("session.list")["active"]
        self.run("document.activate", {"document": index})
        self.run("layer.new.layer", {"name": name})
        self.run("document.activate", {"document": active})

    def save(self, index):
        """The user's ⌘S on the document at `index`."""
        active = self.call("session.list")["active"]
        self.run("document.activate", {"document": index})
        self.call("doc.save", {})
        self.run("document.activate", {"document": active})

    def names(self, index):
        """Every layer's name in the document at `index`, folders' too, as one string."""
        return json.dumps(self.call("doc.inspect", {"index": index})["layers"], ensure_ascii=False)

    def env(self, token=None, root=None):
        return {"PHOTOCRAFT_CONTROL_PORT": str(self.port), "PHOTOCRAFT_CONTROL_TOKEN": token or self.token,
                "PHOTOCRAFT_AUTOMATION_READ_ROOT": root or self.work,
                "PCRAFT_RETOUCH_STALE": os.path.join(self.work, "stale.json")}

    def stop(self):
        self.listener.close()
        self.serve.close()


def on_disk(work, pcraft):
    pc = Pc(work)
    try:
        pc.open(pcraft)
        names = json.dumps([layer["name"] for layer, _ in pc.nodes()], ensure_ascii=False)
        pc.close()
        return names
    finally:
        pc.stop()


def suite_link(work, photo):
    print("== link: the app has the document open, with unsaved work: saved first, loaded again after")
    target, other = os.path.join(work, "linked.pcraft"), os.path.join(work, "other.pcraft")
    skill(work, "--init", photo, "--pcraft", target, "--preset", "studio", "--no-checks")
    skill(work, "--init", photo, "--pcraft", other, "--no-checks")
    pc = Pc(work)
    try:
        pc.open(target)
        ok(not any(layer["name"] == "out.pcraft" for layer, _ in pc.nodes())
           and pc.s.call("session.list")["documents"][0]["name"] == "linked.pcraft",
           "a document made here is named after its file, not the scratch copy it was saved as")
        pc.close()
    finally:
        pc.stop()
    app = FakeApp(work, idle=1.0)  # shorter than a run: one connection kept through it would be dropped
    try:
        app.call("doc.open", {"path": target})
        app.edit(0, "user layer")  # the user's work, not saved yet
        app.call("doc.new", {"width": 64, "height": 64})  # another document in front
        before = app.call("session.list")
        ok(before["documents"][0]["dirty"] and len(before["documents"]) == 2 and before["active"] == 1,
           "the app: the document with unsaved work, another one in front")
        out = skill(work, "--pcraft", target, "--grain", "--no-checks", env=app.env())
        ok("[OK] PhotoCraft: saved linked.pcraft first" in out and "loaded again with the new layers" in out
           and "F12" not in out, "saved there first, loaded again after, no F12 to press")
        after = app.call("session.list")
        doc = after["documents"][0]
        ok(os.path.realpath(doc.get("path", "")).endswith("linked.pcraft") and not doc["dirty"] and after["active"] == 1
           and len(after["documents"]) == 2,
           f"...in its own tab, nothing unsaved, the other document still in front ({after['active']})")
        ok("user layer" in app.names(0) and "🎞️" in app.names(0), "...with the user's unsaved layer and the new grain")
        ok(app.calls.count("file.close") == 1 and app.calls.count("app.save") == 1, f"one save, one close: {app.calls}")

        print("  the path spelled in another case (one file on the Mac's disks):")
        app.edit(0, "second edit")
        out = skill(work, "--pcraft", os.path.join(work, "LINKED.pcraft"), "--sculpt", "--no-checks", env=app.env())
        ok("saved" in out and "loaded again" in out and "second edit" in app.names(0) and "🗿" in app.names(0)
           and not app.documents()[0]["dirty"], "...saved first and loaded again all the same")

        print("  changed in PhotoCraft while the run builds:")
        stamp = os.stat(target).st_mtime_ns
        app.hooks[app.connections + 2] = lambda: app.edit(0, "edited during the run")  # before the write
        out = skill(work, "--pcraft", target, "--look", "film", "--no-checks", env=app.env(), expect=1)
        ok("changed in PhotoCraft while this ran: nothing written" in out and os.stat(target).st_mtime_ns == stamp
           and app.documents()[0]["dirty"], "...the run stops before writing; the change stays there, unsaved")
        out = skill(work, "--pcraft", target, "--look", "film", "--no-checks", env=app.env())
        ok("saved" in out and "loaded again" in out and "edited during the run" in app.names(0)
           and "🎬" in app.names(0), "...run again: the change saved first, the look added, loaded again")

        print("  the document in two tabs, or a smart object's contents in a tab of their own:")
        stamp = os.stat(target).st_mtime_ns
        app.call("doc.open", {"path": target})  # automation can open a file a second time
        out = skill(work, "--pcraft", target, "--glow", "mist", "--no-checks", env=app.env(), expect=1)
        ok("open in 2 tabs" in out and os.stat(target).st_mtime_ns == stamp, "...two tabs of it: the run stops")
        app.run("file.close", {"document": len(app.documents()) - 1})
        smart = next(layer for layer in flat_layers(app.call("doc.inspect", {"index": 0})["layers"])
                     if layer.get("smartContents"))
        active = app.call("session.list")["active"]
        app.run("document.activate", {"document": 0})
        child = app.run("layer.smartObjects.editContents", {"layer": smart["id"]})["document"]
        app.run("document.activate", {"document": active})
        out = skill(work, "--pcraft", target, "--glow", "mist", "--no-checks", env=app.env(), expect=1)
        ok("open in a tab of their own" in out and os.stat(target).st_mtime_ns == stamp,
           "...Edit Contents of one of its smart objects open: the run stops (loading it again would cut that tab loose)")
        app.run("file.close", {"document": child})
        if app.documents()[0]["dirty"]:
            app.save(0)
        stamp = os.stat(target).st_mtime_ns

        print("  the app turns the link away just before the write (past its connection limit), or can't list:")
        app.drop.add(app.connections + 2)
        out = skill(work, "--pcraft", target, "--glow", "mist", "--no-checks", env=app.env(), expect=1)
        ok("couldn't reach it again" in out and "nothing written" in out and os.stat(target).st_mtime_ns == stamp,
           "...a tab seen before the run and none to ask after: the run stops, nothing written")
        app.refuse.add("session.inspect")
        out = skill(work, "--pcraft", target, "--glow", "mist", "--no-checks", env=app.env(), expect=1)
        ok("didn't list its open documents" in out and os.stat(target).st_mtime_ns == stamp,
           "...a dialog open in it: the run stops")
        app.refuse.clear()

        print("  a reload the app refuses (a file it may not open):")
        app.refuse.add("app.open")
        count = len(app.documents())
        out = skill(work, "--pcraft", target, "--glow", "mist", "--no-checks", env=app.env())
        docs = app.documents()
        ok("didn't load linked.pcraft again" in out and "F12" in out and len(docs) == count
           and docs[0].get("path", "").endswith("linked.pcraft") and "file.close" not in app.calls[-3:],
           "...a warning and F12, and the tab stays (it is opened again before the old one is closed)")
        app.refuse.clear()
        print("  changed in PhotoCraft while the reload opens it again:")
        app.before["app.open"] = lambda: app.edit(0, "edited while it opened")
        out = skill(work, "--pcraft", target, "--glow", "halation", "--no-checks", env=app.env())
        docs = app.documents()
        ok("didn't load linked.pcraft again" in out and len(docs) == count and docs[0]["dirty"]
           and "edited while it opened" in app.names(0), "...the new tab closed again, the edited one kept")
        stamp = os.stat(target).st_mtime_ns
        out = skill(work, "--pcraft", target, "--glow", "dream", "--no-checks", env=app.env(), expect=1)
        ok("older than the file" in out and os.stat(target).st_mtime_ns == stamp and "胶片光晕" in on_disk(work, target),
           "...that tab, older than the file, isn't saved over it later: the run stops, the glow stays on disk")
        app.run("file.close", {"document": 0})  # F12: the file loaded again, the edit given up
        app.call("doc.open", {"path": target})
        index = len(app.documents()) - 1
        app.edit(index, "edit after F12")
        out = skill(work, "--pcraft", target, "--glow", "dream", "--no-checks", env=app.env(), expect=1)
        ok("If it was loaded since (F12, or opened again), save it there (⌘S)" in out,
           "...after F12 and a new edit it still stops, and says to ⌘S there first")
        app.save(index)
        out = skill(work, "--pcraft", target, "--glow", "dream", "--no-checks", env=app.env())
        names = app.names(index)
        ok("loaded again" in out and "edit after F12" in names and "胶片光晕" in names
           and "edited while it opened" not in names, "...⌘S there, and the next run goes as usual")

        print("  the document given by a link to it (a symlink):")
        alias = os.path.join(work, "alias.pcraft")
        os.symlink(target, alias)
        out = skill(work, "--pcraft", alias, "--eyes", "--render", "--no-checks", env=app.env())
        ok("loaded again" in out and os.path.islink(alias) and "👁️" in app.names(index) and "👁️" in on_disk(work, target)
           and os.path.isfile(os.path.join(work, "alias.render.jpg")),
           "...the file it points to is written and loaded again; the link stays a link, the render beside it")
        print("  its tab closed while the run builds:")
        app.hooks[app.connections + 2] = lambda: app.run("file.close", {"document": index})
        out = skill(work, "--pcraft", target, "--glow", "mist", "--no-checks", env=app.env())
        stale = json.load(open(os.path.join(work, "stale.json"), encoding="utf-8"))
        now = os.stat(target)
        ok("[SUCCESS]" in out and [now.st_dev, now.st_ino, now.st_mtime_ns, now.st_size] not in stale.values(),
           "...written, and not noted as older than a tab (there is none)")
        app.call("doc.open", {"path": target})
        index = len(app.documents()) - 1

        print("  --init --force over the document open there with unsaved changes:")
        app.edit(index, "unsaved before --init")
        stamp = os.stat(target).st_mtime_ns
        out = skill(work, "--init", photo, "--pcraft", target, "--force", "--no-checks", env=app.env(), expect=1)
        ok("--init --force replaces the document" in out and os.stat(target).st_mtime_ns == stamp,
           "...the run stops, nothing replaced")
        app.run("file.close", {"document": index})

        print("  a document outside the folder the app was given (an external drive), or with a name it refuses:")
        ok(outside_root(os.path.join(work, "con.pcraft"), work) and outside_root(os.path.join(work, "Shoot 11:03", "a.pcraft"), work)
           and outside_root("/Volumes/Card/a.pcraft", work) and not outside_root(os.path.join(work, "a b", "a.pcraft"), work),
           "the app's automation rules: con.pcraft, a `:` in a folder, another disk refused; a space is fine")
        app.call("doc.open", {"path": other})
        index, outside = len(app.documents()) - 1, os.path.join(work, "home")
        os.makedirs(outside)
        app.calls.clear()
        out = skill(work, "--pcraft", other, "--neutral-gray", "--no-checks", env=app.env(root=outside))
        ok("联动只管" in out and "F12" in out and "file.close" not in app.calls and "app.open" not in app.calls
           and app.documents()[index].get("path", "").endswith("other.pcraft"),
           "...a note and F12; its tab is left alone")
        app.edit(index, "unsaved, outside")
        stamp = os.stat(other).st_mtime_ns
        out = skill(work, "--pcraft", other, "--dual-curves", "--no-checks", env=app.env(root=outside), expect=1)
        ok("older than the file" in out and os.stat(other).st_mtime_ns == stamp,
           "...its tab, not loaded again, is older than the file: with unsaved changes there the run stops")
        app.run("file.close", {"document": index})

        app.calls.clear()
        out = skill(work, "--pcraft", other, "--grain", "--no-checks", env=app.env())
        ok("PhotoCraft:" not in out and "F12" in out and "app.save" not in app.calls and "file.close" not in app.calls,
           "a document the app doesn't have open: left alone, F12 as before")
        out = skill(work, "--pcraft", other, "--dual-curves", "--no-checks", env=app.env(token="0" * 64))
        ok("[NOTE] PhotoCraft 联动没连上" in out and "[SUCCESS]" in out, "a wrong token: a note, and the run goes on")
        out = skill(work, "--pcraft", other, "--look", "film", "--no-checks",
                    env={"PHOTOCRAFT_CONTROL_PORT": str(app.port),
                         "PHOTOCRAFT_CONTROL_TOKEN_FILE": os.path.join(work, "none.token")})
        ok("no token file" in out and "[SUCCESS]" in out, "no token file (PhotoCraft never started linked): a note")
    finally:
        app.stop()
    free = socket.create_server(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    out = skill(work, "--pcraft", other, "--sculpt", "--no-checks",
                env={"PHOTOCRAFT_CONTROL_PORT": str(port), "PHOTOCRAFT_CONTROL_TOKEN": "0" * 64})
    ok("联动没连上" not in out and "[SUCCESS]" in out and "F12" in out,
       "nothing listening (PhotoCraft quit): no note, the run goes on (the engine ignores PHOTOCRAFT_CONTROL_PORT)")


def flat_layers(layers):
    for layer in layers:
        yield layer
        yield from flat_layers(layer.get("children") or layer.get("layers") or [])


SUITES = {"dr5": suite_dr5, "looks": suite_looks, "glow": suite_glow, "append": suite_append,
          "refresh": suite_refresh, "errors": suite_errors, "link": suite_link}

if __name__ == "__main__":
    import tempfile
    work = tempfile.mkdtemp(prefix="run-", dir=sys.argv[1])  # a fresh folder each time
    photo = os.path.abspath(sys.argv[2])
    print(f"work folder: {work}")
    for name in sys.argv[3:] or list(SUITES):
        SUITES[name](work, photo)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
