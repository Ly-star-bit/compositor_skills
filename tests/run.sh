#!/bin/bash
# Regression tests for compositor-retouch, all headless: nothing opens a window (no Compositor, no Finder).
#
#   tests/run.sh [suite ...]      suites: format skin-stack folders deep refresh vision glow light render benches heal
#                                         validator  (default: all; "timing" on request)
#   PYTHON=/path/to/python3       the interpreter with the skill's dependencies (default: python3)
#   PYTHON5=/path/to/python3      optional: one with OpenCV 5, to check the fallbacks there
#   KEEP=1                        keep the work folder (projects, logs, check images) instead of deleting it
#
# Test photos (public domain) and the small Swift tools are cached in ~/Library/Caches/compositor-retouch/tests.
# A line containing BAD or FAIL, or a Python traceback, fails its suite; the summary at the end lists them.

HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../scripts/retouch_builder.py"
PY="${PYTHON:-python3}"
CACHE="$HOME/Library/Caches/compositor-retouch/tests"
DATA="$CACHE/data"
BIN="$CACHE/bin"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/compositor-retouch-tests.XXXXXX")"
export TESTDATA="$DATA"
[ "${KEEP:-0}" = 1 ] || trap 'rm -rf "$WORK"' EXIT

"$PY" -c "import numpy, cv2, PIL" 2>/dev/null || {
    echo "$PY lacks the dependencies: $PY -m pip install 'opencv-python-headless<5' pillow numpy"; exit 2; }
"$PY" -c "import ai_edge_litert" 2>/dev/null && MODEL=yes || MODEL=no

# ---- tools and photos ----

tool() {  # tool <Name>: the Swift tool built from tests/<Name>.swift, once per version of its source
    local source="$HERE/$1.swift" hash
    hash=$(shasum "$source" | cut -c1-12)
    if [ ! -x "$BIN/$1-$hash" ]; then
        mkdir -p "$BIN" && swiftc -O -swift-version 5 -o "$BIN/$1-$hash.tmp" "$source" 2>/dev/null \
            && mv "$BIN/$1-$hash.tmp" "$BIN/$1-$hash" || { echo "could not build $1 (swiftc missing?)" >&2; return 1; }
    fi
    echo "$BIN/$1-$hash"
}
adjust_tool() {  # Compositor's adjustment code (tests/AdjustReference.*), built once per version of it
    local hash
    hash=$(cat "$HERE/AdjustReference.c" "$HERE/AdjustReference.h" "$HERE/AdjustReference.swift" | shasum | cut -c1-12)
    if [ ! -x "$BIN/AdjustReference-$hash" ]; then
        mkdir -p "$BIN" && clang -O2 -c "$HERE/AdjustReference.c" -o "$BIN/AdjustReference-$hash.o" 2>/dev/null \
            && swiftc -O -swift-version 5 -import-objc-header "$HERE/AdjustReference.h" "$HERE/AdjustReference.swift" \
                "$BIN/AdjustReference-$hash.o" -o "$BIN/AdjustReference-$hash.tmp" 2>/dev/null \
            && mv "$BIN/AdjustReference-$hash.tmp" "$BIN/AdjustReference-$hash" && rm -f "$BIN/AdjustReference-$hash.o" \
            || { echo "could not build AdjustReference (clang/swiftc missing?)" >&2; return 1; }
    fi
    echo "$BIN/AdjustReference-$hash"
}
fetch() {  # fetch <file> <url>
    [ -s "$DATA/$1" ] && return 0
    mkdir -p "$DATA" && curl -sS -m 300 -f -o "$DATA/$1.tmp" "$2" && mv "$DATA/$1.tmp" "$DATA/$1"
}
fetch astronaut.png https://raw.githubusercontent.com/scikit-image/scikit-image/v0.24.0/skimage/data/astronaut.png || exit 2
fetch collins_orig.jpg "https://images-assets.nasa.gov/image/S99-00858/S99-00858~orig.jpg" || exit 2
for id in S92-40463 jsc2020e032979 jsc2014e037863 jsc2007e036047 jsc2023e047050_alt; do
    fetch "$id.jpg" "https://images-assets.nasa.gov/image/$id/$id~large.jpg" || exit 2
done
VALIDATE=$(tool CompValidator) || exit 2
CG=$(tool CGComposite) || exit 2
TOEXR=$(tool ToEXR) || exit 2
MAKE16=$(tool Make16) || exit 2
ADJUST=$(adjust_tool) || exit 2
PHOTO="$DATA/collins_orig.jpg"
SMALL="$DATA/astronaut.png"

# ---- helpers ----

build() { "$PY" "$S" "$@"; }
want() {  # want <expected exit> <exit> <what>
    if [ "$2" = "$1" ]; then echo "  ok: $3"; else echo "  BAD: $3 (exit $2, wanted $1)"; fi
}
stack() { build --comp "$1" --check | sed -n '2,$p'; }
order() {  # order <project> <label> <prefix>...: BAD unless the top-level layers starting with these prefixes come
    local comp=$1 label=$2; shift 2  # in this order, top to bottom
    stack "$comp" | "$PY" -c "
import sys
label, want = sys.argv[1], sys.argv[2:]
seen = [next(w for w in want if line.strip().startswith(w)) for line in sys.stdin
        if line.startswith('  ') and not line.startswith('    ') and any(line.strip().startswith(w) for w in want)]
print('  ok: ' + label if seen == want else '  BAD: ' + label + ': ' + ' '.join(seen))" "$label" "$@"
}
inside() {  # inside <project> <label> <folder prefix> <prefix>...: BAD unless the layers directly in the top-level
    local comp=$1 label=$2 folder=$3; shift 3  # folders starting with <folder prefix> start with these, top to bottom
    stack "$comp" | "$PY" -c "
import sys
label, folder, want = sys.argv[1], sys.argv[2], sys.argv[3:]
seen, within = [], False
for line in sys.stdin:
    if line.startswith('  ') and not line.startswith('   '):
        within = line.strip().startswith(folder)
    elif within and line.startswith('      ') and not line.startswith('       '):
        seen += [w for w in want if line.strip().startswith(w)][:1]  # one entry per layer, so a prefix may repeat
print('  ok: ' + label if seen == want else '  BAD: ' + label + ': ' + ' '.join(seen))" "$label" "$folder" "$@"
}
flatten() {  # flatten <project>...: the layout earlier versions wrote, the same layers without the group folders
    "$PY" - "$@" <<'EOF'
import json, sys
MARKS = ("🎚️", "🧴", "🌓", "😊", "🏞️", "🌟")
for comp in sys.argv[1:]:
    path = f"{comp}/manifest.json"; manifest = json.load(open(path))
    folders = {l["id"]: l.get("parentID") for l in manifest["layers"] if l.get("isGroup") and l["name"].startswith(MARKS)}
    layers = []
    for layer in manifest["layers"]:
        if layer["id"] in folders:
            continue
        if layer.get("parentID") in folders:
            parent = folders[layer.pop("parentID")]
            if parent:
                layer["parentID"] = parent
        layers.append(layer)
    manifest["layers"] = layers
    json.dump(manifest, open(path, "w"), ensure_ascii=False, indent=2)
EOF
}
reduce_noise() {  # reduce_noise <project> <version> [strength]: a Reduce Noise layer over the photo, in the shape
    "$PY" - "$@" <<'EOF'  # Compositor 1.6 saves (a filter layer like the project's Sharpen, its settings in reduceNoise)
import copy, json, sys, uuid
path = sys.argv[1] + "/manifest.json"; m = json.load(open(path))
layer = copy.deepcopy(next(l for l in m["layers"] if (l.get("adjustment") or {}).get("kind") == "Sharpen"))
for key in ("maskFile", "maskEnabled", "parentID"):
    layer.pop(key, None)
layer.update(id=str(uuid.uuid4()).upper(), name="Reduce Noise", opacity=1.0)
layer["adjustment"]["kind"] = "Reduce Noise"
settings = layer["adjustment"]["filterLayerSettings"]; settings.pop("sharpen")
settings["reduceNoise"] = {"strength": float(sys.argv[3]) if len(sys.argv) > 3 else 50, "details": 50, "color": 50}
m["layers"].insert(1, layer); m["version"] = int(sys.argv[2])
json.dump(m, open(path, "w"), ensure_ascii=False)
EOF
}
has() {  # has <label> <command...>: BAD unless the command prints something
    local label=$1; shift
    if [ -n "$("$@" 2>/dev/null)" ]; then echo "  ok: $label"; else echo "  BAD: $label"; fi
}
lacks() {  # lacks <label> <command...>: BAD if the command prints something
    local label=$1; shift
    if [ -z "$("$@" 2>/dev/null)" ]; then echo "  ok: $label"; else echo "  BAD: $label"; fi
}
validate() { "$VALIDATE" "$@" | cut -c1-150; }

# ---- suites ----

suite_format() {  # writing, refusing and rolling back
    build --init "$SMALL" --comp q.comp --neutral-gray --inspection > /dev/null 2>&1; want 0 $? "neutral gray and the observation folders"
    build --comp q.comp --freq-sep --delicious-skin --eyes --inspection > add.log 2>&1; want 0 $? "modules added to it"
    has "...the eyes a pixel layer in a format-11 project, saying why" grep -m1 "eye brilliance is a pixel layer: the project is format version 11" add.log
    has "a second observation folder is skipped" grep -m1 SKIP add.log
    build --comp q.comp --freq-sep > again.log 2>&1; want 0 $? "frequency separation again"
    has "...and skipped" grep -m1 SKIP again.log
    local before after id1 id2
    before=$(ls q.comp/images | wc -l)
    build --init "$SMALL" --comp q.comp --freq-sep --delicious-skin > /dev/null 2>&1; want 1 $? "--init on an existing project refused"
    after=$(ls q.comp/images | wc -l); [ "$before" = "$after" ] && echo "  ok: nothing written" || echo "  BAD: files changed"
    id1=$("$PY" -c "import json;print(json.load(open('q.comp/manifest.json'))['documentID'])")
    mkdir -p q.comp/QuickLook && touch q.comp/QuickLook/Preview.jpg
    build --init "$SMALL" --comp q.comp --force --freq-sep --delicious-skin > /dev/null 2>&1; want 0 $? "--force replaces it"
    id2=$("$PY" -c "import json;print(json.load(open('q.comp/manifest.json'))['documentID'])")
    [ "$id1" = "$id2" ] && echo "  ok: documentID kept" || echo "  BAD: documentID changed"
    [ -d q.comp/QuickLook ] && echo "  BAD: stale QuickLook kept" || echo "  ok: QuickLook removed"
    "$PY" -c "
import json, os
m = json.load(open('q.comp/manifest.json'))
used = {l.get(k) for l in m['layers'] for k in ('imageFile', 'maskFile')} - {None}
print('  ok: unreferenced images pruned' if set(os.listdir('q.comp/images')) == used else '  BAD: stray images left')"
    build --comp missing.comp --eyes > /dev/null 2>&1; want 1 $? "a module on a missing project"
    [ -e missing.comp ] && echo "  BAD: missing.comp created" || echo "  ok: nothing created"
    build --comp q.comp --preset dr5 > /dev/null 2>&1; want 1 $? "dr5 without --init"
    build --comp q.comp --preset studio > /dev/null 2>&1; want 1 $? "studio without --init"
    for gone in heal auto-db shine even-tone teeth dark-circles eye-whites lips background; do
        build --comp q.comp "--$gone" > /dev/null 2>&1; want 2 $? "--$gone is no option any more"
    done
    build --comp q.comp --neutral-gray --without heal > /dev/null 2>&1; want 1 $? "--without a module that is gone"
    cp -R q.comp broken.comp && cp broken.comp/manifest.json before.json
    "$PY" -c "import json;p='broken.comp/manifest.json';m=json.load(open(p));m['layers'][0]['blendMode']='SoftLight';json.dump(m,open(p,'w'))"
    cp broken.comp/manifest.json before.json; before=$(ls broken.comp/images | wc -l)
    build --comp broken.comp --eyes --grain > /dev/null 2>&1; want 1 $? "a broken manifest refused"
    cmp -s broken.comp/manifest.json before.json && [ "$before" = "$(ls broken.comp/images | wc -l)" ] \
        && echo "  ok: rolled back" || echo "  BAD: broken project changed"
    build --comp q.comp > /dev/null 2>&1; want 1 $? "nothing to do"
    build --init nope.jpg --comp new.comp > /dev/null 2>&1; want 1 $? "unreadable input"
    [ -e new.comp ] && echo "  BAD: new.comp left behind" || echo "  ok: nothing left behind"
    build --comp q.comp --delicious-skin --strength 3 > /dev/null 2>&1; want 1 $? "strength out of range"
    build --comp q.comp --delicious-skin --skin-strength 0 > /dev/null 2>&1; want 1 $? "skin strength out of range"
    "$PY" - "$S" <<'EOF'
# Compositor saves the project while a run is under way: commit refuses to overwrite the newer manifest.
import json, os, sys
from PIL import Image
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
p = rb.Project.open(os.path.abspath("q.comp"))
path = "q.comp/manifest.json"; m = json.load(open(path)); m["layers"][0]["isVisible"] = False
json.dump(m, open(path, "w"), ensure_ascii=False, indent=2)
saved = open(path, "rb").read()
before = sorted(os.listdir("q.comp/images"))
p.put("light", [p.pixel_layer("🧪 试", Image.new("RGBA", (p.width, p.height), (128, 128, 128, 255)))])
p.finish_saving()
print("  ok: the new layer waits outside images/ until commit" if os.path.isdir("q.comp/.retouch") and sorted(os.listdir("q.comp/images")) == before
      else "  BAD: images/ changed before commit")
import shutil; shutil.rmtree("q.comp/.retouch")  # Compositor's save replaces the whole package, the waiting files with it
try:
    p.commit(); print("  BAD: commit overwrote a project saved meanwhile")
except rb.BuildError as error:
    p.discard()
    print("  ok: commit refuses a project saved meanwhile" if "changed on disk" in str(error) else f"  BAD: wrong error: {error}")
left = [n for n in os.listdir("q.comp") if n.endswith((".new", ".old", ".tmp")) or n == ".retouch"]
stray = [n for n in os.listdir("q.comp/images") if not any(n in (l.get("imageFile"), l.get("maskFile")) for l in m["layers"])]
print("  ok: ...the saved manifest is untouched, nothing left over" if open(path, "rb").read() == saved and not left and not stray
      else f"  BAD: manifest changed {open(path, 'rb').read() != saved}, left {left}, stray {stray}")
EOF
    build --comp q.comp --check > /dev/null 2>&1; want 0 $? "...and the project still opens"
    validate q.comp
    build --init "$SMALL" --comp b.comp --inspection --no-checks > /dev/null 2>&1; want 0 $? "a project to add to"
    "$PY" - "$S" <<'EOF'
# Compositor fingerprints an open project by its manifest bytes and the names and sizes of the files in images/, and
# reloads the whole project on each change it sees. Sampled every 50 ms while a build adds a pixel layer, masks and
# adjustments, the fingerprint must change in one cluster: everything lands within its 0.3 s coalescing window.
import hashlib, os, subprocess, sys, time
def digest(comp):
    h = hashlib.sha256()
    try:
        h.update(open(f"{comp}/manifest.json", "rb").read())
        for name in sorted(os.listdir(f"{comp}/images")):
            st = os.stat(f"{comp}/images/{name}"); h.update(name.encode()); h.update(st.st_size.to_bytes(8, "little"))
    except OSError:
        return None
    return h.hexdigest()
proc = subprocess.Popen([sys.executable, sys.argv[1], "--comp", "b.comp", "--neutral-gray", "--dual-curves", "--glow", "--no-checks"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
changes, last = [], digest("b.comp")
while proc.poll() is None:
    time.sleep(0.05)
    now = digest("b.comp")
    if now != last:
        changes.append(time.time()); last = now
if digest("b.comp") != last:
    changes.append(time.time())
clusters = (1 + sum(1 for a, b in zip(changes, changes[1:]) if b - a > 0.3)) if changes else 0
print(f"  {'ok' if proc.returncode == 0 and clusters == 1 else 'BAD'}: Compositor would reload once "
      f"({len(changes)} fingerprint changes in {clusters} cluster(s), exit {proc.returncode})")
print("  ok: nothing waiting left behind" if not os.path.exists("b.comp/.retouch") else "  BAD: .retouch left in the package")
EOF
    validate b.comp
}

reveal() {  # reveal <project> <folder prefix>: show a hidden folder (an observation folder) for a render
    "$PY" -c "
import json, sys; p = sys.argv[1] + '/manifest.json'; m = json.load(open(p))
for l in m['layers']:
    if l['name'].startswith(sys.argv[2]): l['isVisible'] = True
json.dump(m, open(p, 'w'), ensure_ascii=False)" "$1" "$2"
}

suite_skin_stack() {  # the layers stack in order, on new and existing projects
    build --init "$PHOTO" --comp a.comp --delicious-skin auto --sculpt --neutral-gray --dual-curves --eyes --inspection \
        > a.log 2>&1; want 0 $? "every module on a new project"
    order a.comp "folders: skin, light, features, the two observation folders on top" "🔍 观察组 · 黑白" "🔍 观察组 · 颜色" 😊 🌓 🧴 原图
    inside a.comp "skin folder: the smoothing" 🧴 🔥
    inside a.comp "light folder: dual curves over neutral gray over sculpt" 🌓 ☀️ 🌑 🩶 🗿
    inside a.comp "eye brilliance in the features folder" 😊 👁️
    has "the sculpt is a live Tonal Contrast layer through its mask" grep -m1 "🗿.*adjustment:Tonal Contrast, Normal, 100%, mask" a.log
    has "the eye brilliance is a live Sharpen layer at 70% through its mask" grep -m1 "👁️.*adjustment:Sharpen, Normal, 70%, mask" a.log
    "$PY" -c "import json; v = json.load(open('a.comp/manifest.json'))['version']; print('  ok: a new project with them is format 19' if v == 19 else f'  BAD: version {v}')"
    build --init "$SMALL" --comp compat.comp --8bit --sculpt --eyes --no-checks > compat.log 2>&1; want 0 $? "--8bit: only what Compositor before 1.5 reads"
    has "...the sculpt a Soft Light pixel layer" grep -m1 "🗿.*\[pixels, Soft Light, 60%, mask" compat.log
    has "...the eyes a pixel layer" grep -m1 "👁️.*\[pixels, Normal, 70%, mask" compat.log
    has "...saying why" grep -m1 "is a pixel layer: the project is format version 11" compat.log
    "$PY" -c "import json; v = json.load(open('compat.comp/manifest.json'))['version']; print('  ok: ...format 11' if v == 11 else f'  BAD: version {v}')"
    has "the smoothing says how it is masked" grep -m1 "保边磨皮 · 保形.*面部锁定" a.log
    echo "  spots the smoothing healed first: $(grep -oE '祛痘 [0-9]+ 处' a.log | head -1)"
    [ -f a.checks/features.jpg ] && echo "  ok: features.jpg (sculpt and eyes painted in)" || echo "  BAD: no features.jpg"
    [ -f a.checks/masks.jpg ] && [ -f a.checks/before_after.jpg ] && echo "  ok: masks.jpg, before_after.jpg" || echo "  BAD: check images missing"
    has "overview.jpg is the first check image reported" grep -m1 "Check image: .*overview.jpg" a.log
    "$PY" -c "
from PIL import Image; im = Image.open('a.checks/overview.jpg')
print('  ok: overview.jpg is one sheet, %dx%d' % im.size if im.size[0] == 1600 and 1200 <= im.size[1] <= 2600 else '  BAD: overview.jpg %dx%d' % im.size)"
    [ -d a.checks ] && echo "  ok: checks: $(ls a.checks | tr '\n' ' ')" || echo "  BAD: no checks written"
    build --init "$PHOTO" --comp d.comp --preset dr5 --delicious-skin auto --no-checks > d.log 2>&1; want 0 $? "dr5"
    inside d.comp "low and high in the frequency-separation folder" 🎚️ 高频 低频
    order d.comp "dr5: one folder each, frequency separation right above the photo" "🔍 观察组 · 黑白" "🔍 观察组 · 颜色" 🎞️ \
        😊 🌓 🧴 🎚️ 原图
    inside d.comp "dr5's light folder: the dual curves over the sculpt" 🌓 ☀️ 🌑 🗿
    lacks "no teeth layer in dr5 any more" grep 🦷 d.log
    build --comp d.comp --delicious-skin manual --neutral-gray --no-checks > d2.log 2>&1; want 0 $? "manual smoothing and neutral gray added to dr5"
    inside d.comp "...the manual smoothing on top of dr5's skin folder" 🧴 🔥 🔥
    has "...with its black mask, marked by the ✋" grep -m1 "🔥✋.*黑蒙版 · 画笔涂白处磨皮" d2.log
    inside d.comp "...the neutral gray on top of its light folder" 🌓 🩶 ☀️ 🌑 🗿
    "$PY" -c "
import json; p = 'd.comp/manifest.json'; m = json.load(open(p))
for l in m['layers']:
    if l['name'].startswith('🔥✋'): l['name'] = '🔥✋ 手动磨皮'
    elif l['name'].startswith('🔥'): l['name'] = '🔥 磨皮'
json.dump(m, open(p, 'w'), ensure_ascii=False)"
    build --comp d.comp --preset daily --no-checks > renamed.log 2>&1; want 0 $? "daily again after renaming both smoothing layers (emoji kept)"
    lacks "...adds no layer" grep '^\[OK\] +' renamed.log
    [ "$(grep -c '重做 · 保留蒙版' renamed.log)" = 1 ] && echo "  ok: ...the renamed automatic layer is re-made in place" || echo "  BAD: $(grep -c '重做' renamed.log) re-made"
    build --comp d.comp --delicious-skin manual --no-checks > renamed2.log 2>&1
    has "...and so is the renamed manual one, still manual" grep -m1 "~ 🔥✋.*重做" renamed2.log
    inside d.comp "...still two smoothing layers, no third" 🧴 🔥 🔥
    build --init "$PHOTO" --comp st.comp --preset studio --no-checks > /dev/null 2>&1; want 0 $? "studio"
    order st.comp "studio: light folder and frequency separation, observation folders on top" "🔍 观察组 · 黑白" "🔍 观察组 · 颜色" 🌓 🎚️ 原图
    inside st.comp "studio's light folder: the dual curves over the neutral gray" 🌓 ☀️ 🌑 🩶
    build --comp d.comp --preset dr5 --without grain > /dev/null 2>&1; want 1 $? "dr5 on an existing project is refused"
    build --init "$PHOTO" --comp day.comp --preset daily --delicious-skin auto > day.log 2>&1; want 0 $? "daily on a photo"
    order day.comp "daily: skin and light folders, the observation folders on top" "🔍 观察组 · 黑白" "🔍 观察组 · 颜色" 🌓 🧴 原图
    inside day.comp "...the neutral gray in the light folder" 🌓 🩶
    inside day.comp "...the smoothing in the skin folder" 🧴 🔥
    build --comp day.comp --preset daily --dual-curves --sculpt --grain --inspection --look film --no-checks > day2.log 2>&1
    want 0 $? "daily again on the same project, with more modules"
    [ "$(grep -c 'SKIP\] --neutral-gray' day2.log)" = 1 ] && echo "  ok: ...the neutral gray is not added twice" || echo "  BAD: neutral gray added again"
    has "...the smoothing is made again in place instead" grep -m1 "重做 · 保留蒙版" day2.log
    has "...the observation folders are skipped" grep -m1 "observation folders already exist" day2.log
    build --comp day.comp --preset daily --dual-curves --sculpt --grain --look film --no-checks > day3.log 2>&1
    want 0 $? "and a third time"
    [ "$(grep -c '^\[OK\] +' day3.log)" = 0 ] && echo "  ok: ...nothing added the third time" || { echo "  BAD: layers added again:"; grep '^\[OK\] +' day3.log; }
    has "...the dual curves are skipped" grep -m1 "SKIP\] --dual-curves" day3.log
    has "...the sculpt, the grain and the grade too" grep -m1 "SKIP\] --grain" day3.log
    "$PY" - <<'EOF'
import json
layers = json.load(open("day.comp/manifest.json"))["layers"]
counts = {mark: sum(1 for l in layers if l["name"].startswith(mark)) for mark in ("🩶", "🔥", "🗿", "🎞️", "☀️", "🌑", "🎬")}
print(f"  {'ok' if all(n == 1 for n in counts.values()) else 'BAD'}: one of each after three runs {counts}")
EOF
    "$PY" -c "
import json; m = json.load(open('day.comp/manifest.json'))
l = next(l for l in m['layers'] if l['name'].startswith('🔥')); print(l['imageFile'], l.get('maskFile'), l['opacity'])" > ids_before.txt
    build --comp day.comp --delicious-skin --skin-strength 0.6 --no-checks > light.log 2>&1; want 0 $? "--skin-strength 0.6 on the project"
    "$PY" - <<'EOF'
import json
m = json.load(open("day.comp/manifest.json"))
l = next(l for l in m["layers"] if l["name"].startswith("🔥"))
image, mask, opacity = open("ids_before.txt").read().split()
print(f"  {'ok' if l['imageFile'] == image and l.get('maskFile') == mask else 'BAD'}: the same layer, the same mask file")
print(f"  {'ok' if abs(l['opacity'] - 0.53) < 0.011 and '力度 0.6' in l['name'] else 'BAD'}: ...at the lighter strength "
      f"(opacity {l['opacity']}, name says {'力度 0.6' in l['name']})")
EOF
    build --comp day.comp --delicious-skin manual --no-checks > manual2.log 2>&1; want 0 $? "a manual smoothing beside the automatic one"
    inside day.comp "...both in the skin folder, the manual one on top" 🧴 🔥 🔥
    has "...with its black mask" grep -m1 "黑蒙版 · 画笔涂白处磨皮" manual2.log
    build --init "$PHOTO" --comp w.comp --preset dr5 --without grain --without sculpt --no-checks > w.log 2>&1; want 0 $? "--without"
    lacks "...leaves the grain and the sculpt out" grep -E "🎞️|🗿" w.log
    build --init "$PHOTO" --comp n.comp --freq-sep --delicious-skin --no-checks > /dev/null 2>&1
    [ -d n.checks ] && echo "  BAD: --no-checks wrote checks" || echo "  ok: --no-checks writes none"
    build --init "$PHOTO" --comp m.comp --delicious-skin manual --no-checks > m.log 2>&1; want 0 $? "--delicious-skin manual"
    has "...its mask starts black, to brush on" grep -m1 "保边磨皮 · 保形.*黑蒙版" m.log
    "$PY" - "$S" <<'EOF'
# A manual layer shows smoothed skin wherever it is brushed: beyond the face where the person's skin goes on (neck,
# hands), and the photo itself where there is no skin, never the filter's residue.
import os, sys, numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
p = rb.Project.open("m.comp")
layer = next(l for l in p.layers if l["name"].startswith("🔥"))
pixels = np.asarray(Image.open(f"m.comp/images/{layer['imageFile']}").convert("RGB")).astype(int)
photo = p.base_rgb().astype(int)
skin, _ = p.skin(); reach = p.skin_reach()
diff = np.abs(pixels - photo).max(axis=-1)
print(f"  {'ok' if diff[reach == 0].max() <= 1 else 'BAD'}: beyond the people's skin the layer is the photo (max difference {diff[reach == 0].max()})")
feather = (reach > 0) & (reach < 128)
print(f"  {'ok' if diff[feather].max() <= 1 else 'BAD'}: ...and so is the outer half of the mask's feather: no fringe along "
      f"hairline or collar (max difference {diff[feather].max()})")
extra = (reach > 200) & (skin < 30)
if extra.sum() > 500:
    print(f"  {'ok' if (diff[extra] > 0).mean() > 0.2 else 'BAD'}: ...and skin beyond the face mask is smoothed too "
          f"({extra.sum()} px, {(diff[extra] > 0).mean() * 100:.0f}% changed)")
else:
    print(f"  (this photo has no skin beyond the face mask to check: {extra.sum()} px)")
EOF
    "$PY" - <<'EOF'
import json, numpy as np
from PIL import Image
m = json.load(open("m.comp/manifest.json"))
layer = next(l for l in m["layers"] if l["name"].startswith("🔥"))
mask = np.asarray(Image.open(f"m.comp/images/{layer['maskFile']}").convert("L"))
print("  ok: ...a black mask, so nothing shows until it is brushed in" if mask.max() == 0 and layer.get("maskEnabled", True)
      else f"  BAD: manual mask max {mask.max()}, enabled {layer.get('maskEnabled')}")
print(f"  ok: ...at the usual opacity ({layer['opacity']})" if abs(layer["opacity"] - 0.88) < 0.01 else f"  BAD: opacity {layer['opacity']}")
EOF
    build --init "$PHOTO" --comp g.comp --delicious-skin --engine guided --no-checks > g.log 2>&1; want 0 $? "--engine guided"
    has "...the earlier engine" grep -m1 "保边磨皮 · 导向滤波" g.log
    build --init "$PHOTO" --comp t.comp --delicious-skin --texture soft --no-checks > t.log 2>&1; want 0 $? "--texture soft"
    has "...named in the layer" grep -m1 "保边磨皮 · 保形.*质感柔和" t.log
    "$PY" -c "
from PIL import Image
photo = Image.open('$PHOTO').convert('RGB'); w, h = photo.size
small = photo.resize((round(w / 1.5), round(h / 1.5)), Image.LANCZOS)
frame = Image.new('RGB', (w, h), (235, 235, 235)); frame.paste(small, ((w - small.width) // 2, h - small.height))
frame.save('framed.jpg', quality=95)
Image.new('RGB', (900, 600), (180, 150, 130)).save('noface.png')"
    for x in "$PHOTO:full" framed.jpg:framed noface.png:noface; do
        build --init "${x%%:*}" --comp "fs_${x##*:}.comp" --freq-sep --no-checks > /dev/null 2>&1
    done
    build --init "$PHOTO" --comp fs_manual.comp --freq-sep --blur-radius 4 --no-checks > /dev/null 2>&1
    "$PY" - <<'EOF'
import json, re
sigma = {}
for name in ("full", "framed", "noface", "manual"):
    low = next(l["name"] for l in json.load(open(f"fs_{name}.comp/manifest.json"))["layers"] if l["name"].startswith("低频"))
    sigma[name] = (float(re.search(r"σ=([0-9.]+)px", low).group(1)), low.split(" · ")[-1].rstrip(")"))
print(f"  σ: {sigma}")
ok = sigma["full"][1] == "按脸宽" and abs(sigma["framed"][0] / sigma["full"][0] - 1 / 1.5) < 0.1
print(f"  {'ok' if ok else 'BAD'}: frequency separation sized to the face: 1.5x smaller in the frame, 1.5x smaller σ")
print(f"  {'ok' if sigma['noface'][1] == '按分辨率' and sigma['manual'] == (4.0, '手动') else 'BAD'}: "
      "by the resolution without a face; --blur-radius as given")
EOF
    # Another radius on a pair no one has worked on makes it again in place; the same one, or hand work on it, leaves it.
    pair() {  # pair <project>: the low layer's name, both ids, and whether low + 2 * high - 255 is still the photo
        "$PY" - "$S" "$1" <<'EOF'
import json, sys, numpy as np
from PIL import Image
sys.argv = [sys.argv[1]] + sys.argv[2:]; sys.path.insert(0, __import__("os").path.dirname(sys.argv[0]))
import retouch_builder as rb
p = rb.Project.open(sys.argv[1]); low, high = p.frequency_pair()
planes = [np.asarray(Image.open(p.file_path(r["imageFile"])).convert("RGB")).astype(int) for r in (low, high)]
error = np.abs(planes[0] + 2 * planes[1] - 255 - p.base_rgb().astype(int)).max()
print(low["name"], low["id"], high["id"], error)
EOF
    }
    before=$(pair fs_manual.comp)
    build --comp fs_manual.comp --freq-sep --blur-radius 7 --no-checks > fsr.log 2>&1; want 0 $? "--blur-radius 7 on a pair made at 4"
    after=$(pair fs_manual.comp)
    "$PY" -c "
import sys; b, a = sys.argv[1].rsplit(' ', 3), sys.argv[2].rsplit(' ', 3)
ok = 'σ=7px · 手动' in a[0] and b[1:3] == a[1:3] and int(a[3]) <= 1
print(f'  {\"ok\" if ok else \"BAD\"}: ...made again at 7 in place, the same layers, still the photo together '
      f'({a[0][-22:]}, off by {a[3]})')" "$before" "$after"
    build --comp fs_manual.comp --freq-sep --blur-radius 7 --no-checks > fsr2.log 2>&1
    has "...the same radius again: skipped" grep -m1 "SKIP.*already σ=7px" fsr2.log
    build --comp fs_manual.comp --freq-sep --blur-radius auto --no-checks > fsr3.log 2>&1
    has "...auto: back to the face's width" grep -m1 "低频.*按脸宽.*(重做" fsr3.log
    build --comp fs_manual.comp --freq-sep --blur-radius auto --no-checks > fsr4.log 2>&1
    has "...auto on a pair sized by the face: skipped, not measured again" grep -m1 "SKIP.*already sized automatically" fsr4.log
    build --comp fs_manual.comp --freq-sep --no-checks > fsr5.log 2>&1
    has "...no radius given: skipped as before" grep -m1 "SKIP.*already sits above the photo" fsr5.log
    "$PY" - <<'EOF'
import json, cv2
m = json.load(open("fs_manual.comp/manifest.json")); high = next(l for l in m["layers"] if l["name"].startswith("高频"))
path = f"fs_manual.comp/images/{high['imageFile']}"; px = cv2.imread(path, cv2.IMREAD_UNCHANGED)
px[1500:1506, 1300:1306, :3] = 128  # one small dab of the clone stamp
cv2.imwrite(path, px)
EOF
    cp fs_manual.comp/manifest.json fsr_before.json; ls -l fs_manual.comp/images > fsr_before.txt
    build --comp fs_manual.comp --freq-sep --blur-radius 9 --no-checks > fsr6.log 2>&1; want 0 $? "--blur-radius 9 after a dab on the high layer"
    has "...says the pair was worked on and keeps it" grep -m1 "WARN.*has been worked on" fsr6.log
    if cmp -s fsr_before.json fs_manual.comp/manifest.json && ls -l fs_manual.comp/images | cmp -s - fsr_before.txt; then
        echo "  ok: ...nothing changed"; else echo "  BAD: the worked-on pair was changed"; fi
    # --blur-radius sizes the frequency separation only: the smoothing is the same with or without it
    build --init "$SMALL" --comp br0.comp --freq-sep --delicious-skin --no-checks > /dev/null 2>&1
    build --init "$SMALL" --comp br1.comp --freq-sep --delicious-skin --blur-radius 12 --no-checks > /dev/null 2>&1
    "$PY" - <<'EOF'
import json, numpy as np
from PIL import Image
def smoothing(comp):
    layer = next(l for l in json.load(open(f"{comp}/manifest.json"))["layers"] if l["name"].startswith("🔥"))
    return layer["name"], np.asarray(Image.open(f"{comp}/images/{layer['imageFile']}").convert("RGB")).astype(int)
(n0, a), (n1, b) = smoothing("br0.comp"), smoothing("br1.comp")
print(f"  {'ok' if np.abs(a - b).max() == 0 else 'BAD'}: --blur-radius leaves the smoothing alone "
      f"(max difference {np.abs(a - b).max()})")
print(f"  {'ok' if n0.startswith('🔥✋') and '黑蒙版' in n0 else 'BAD'}: --delicious-skin alone brushes it on by hand: {n0[:24]}")
EOF
    build --init "$SMALL" --comp dd.comp --preset daily --no-checks > dd.log 2>&1
    has "the daily preset's smoothing is brushed on by hand too" grep -m1 "+ 🔥✋.*黑蒙版" dd.log
    build --comp dd.comp --delicious-skin auto --no-checks > /dev/null 2>&1
    build --comp dd.comp --preset daily --no-checks > dd2.log 2>&1
    "$PY" -c "
import json; names = [l['name'] for l in json.load(open('dd.comp/manifest.json'))['layers'] if l['name'].startswith('🔥')]
print(f'  {\"ok\" if len(names) == 2 else \"BAD\"}: ...with both kinds there, daily re-makes one, adding none '
      f'({len(names)} smoothing layers)')"
    has "...the hand-brushed one, the default" grep -m1 "~ 🔥✋.*重做" dd2.log
    "$PY" "$HERE/heal_eval.py" make "$PHOTO" strong.jpg strong.json strong > /dev/null
    build --init strong.jpg --comp observe.comp --inspection --no-checks > /dev/null 2>&1; want 0 $? "--inspection"
    "$PY" -c "
import json; p = 'observe.comp/manifest.json'; m = json.load(open(p))
print('  ok: two observation folders, both hidden' if [l['isVisible'] for l in m['layers'] if l['name'].startswith('🔍')] == [False, False] else '  BAD: observation folders')
for l in m['layers']:
    if l['name'].startswith('🔍 观察组 · 颜色'): l['isVisible'] = True
json.dump(m, open(p, 'w'), ensure_ascii=False)"
    build --comp observe.comp --render observe.png > /dev/null 2>&1
    build --init strong.jpg --comp observelook.comp --look film --inspection --no-checks > /dev/null 2>&1; want 0 $? "--look film --inspection together"
    reveal observelook.comp "🔍 观察组 · 颜色"
    build --comp observelook.comp --render observelook.png > /dev/null 2>&1
    "$PY" - "$S" "$HERE" <<'EOF'
# The color view: the face's usual skin color reads gray, the planted red spots red; with a grade under the
# observation folders too, since they are calibrated on what is drawn under them.
import json, os, sys, numpy as np, cv2
from PIL import Image
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
sys.path.insert(0, sys.argv[2]); from heal_eval import contrast
for name, comp, label in (("observe.png", "observe.comp", "plain"), ("observelook.png", "observelook.comp", "under a film grade")):
    skin = rb.Project.open(comp).skin()[0]  # the mask the folder was calibrated on (person matte, model)
    view = np.asarray(Image.open(name).convert("RGB")).astype(np.float32) / 255
    center = np.median(view[skin > 200] * 255, axis=0)
    gray = max(abs(center[0] - center[1]), abs(center[1] - center[2]), abs(center[0] - center[2]))
    print(f"  {'ok' if gray < 12 else 'BAD'}: the usual skin reads gray in the color view, {label} (median {np.round(center)})")
    red = [contrast(view, *s[:3])[1] for s in json.load(open("strong.json"))["spots"] if s[3] == "red"]
    print(f"  {'ok' if np.mean(red) > 8 else 'BAD'}: red spots stand out in it, {label} (a* {np.mean(red):.1f} over their ring)")
EOF
    for img in "$PHOTO" "$DATA/S92-40463.jpg"; do
        n=$(basename "${img%.*}")
        build --init "$img" --comp "bw_$n.comp" --neutral-gray --inspection --no-checks > /dev/null 2>&1
        reveal "bw_$n.comp" "🔍 观察组 · 黑白"
        build --comp "bw_$n.comp" --render "bw_$n.png" > /dev/null 2>&1; want 0 $? "the black & white view of $n"
        "$PY" - "$S" "bw_$n.comp" "bw_$n.png" <<'EOF'
# The black & white view puts this face's skin at middle gray, whatever its tone.
import os, sys, numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
skin = rb.Project.open(sys.argv[2]).skin()[0]  # the mask the curve was calibrated on
view = np.asarray(Image.open(sys.argv[3]).convert("L")).astype(np.float32)
median = float(np.median(view[skin > 200]))
print(f"  {'ok' if abs(median - 128) <= 25 else 'BAD'}: ...skin at middle gray (median {median:.0f})")
EOF
    done
    for s in 0.5 1.5; do
        build --init strong.jpg --comp "s$s.comp" --delicious-skin --strength $s --no-checks 2>&1 | grep -oE "祛痘 [0-9]+ 处" | head -1 | sed "s/^/  strength $s: /"
    done
    has "a strength other than 1 is in the layer name" grep -m1 "力度 0.5" <(stack s0.5.comp)
    validate a.comp d.comp day.comp st.comp w.comp n.comp m.comp g.comp t.comp s0.5.comp s1.5.comp observelook.comp bw_*.comp
}

suite_folders() {  # projects from before the folders, a hidden folder, and folders not changing the picture
    build --init "$PHOTO" --comp flat.comp --preset dr5 --delicious-skin auto --look film --glow halation --no-checks \
        > /dev/null 2>&1; want 0 $? "a project with every kind of group"
    "$PY" "$S" --comp flat.comp --render foldered.png > /dev/null 2>&1
    flatten flat.comp
    "$PY" "$S" --comp flat.comp --render flat.png > /dev/null 2>&1
    "$PY" -c "
import numpy as np; from PIL import Image
a, b = (np.asarray(Image.open(f)) for f in ('foldered.png', 'flat.png'))
print('  ok: the folders don\'t change the picture' if np.array_equal(a, b) else '  BAD: folders changed the render')"
    "$PY" - <<'EOF'
# Earlier versions also made an automatic dodge & burn as a gray Soft Light layer in the skin stack.
import json, uuid
from PIL import Image
path = "flat.comp/manifest.json"; manifest = json.load(open(path))
at = next(i for i, layer in enumerate(manifest["layers"]) if layer["name"].startswith("🔥"))
gray, uid = dict(manifest["layers"][at]), str(uuid.uuid4()).upper()
gray.update(id=uid, name="🌗 自动中性灰 (柔光 · 可继续用画笔涂亮/涂暗)", blendMode="Soft Light", opacity=1, imageFile=f"{uid}.png")
gray.pop("maskFile", None); gray.pop("maskEnabled", None)
Image.new("RGBA", (manifest["width"], manifest["height"]), (128, 128, 128, 255)).save(f"flat.comp/images/{uid}.png")
manifest["layers"].insert(at + 1, gray)
json.dump(manifest, open(path, "w"), ensure_ascii=False, indent=2)
EOF
    order flat.comp "flattened the way earlier versions wrote it" "🔍 观察组 · 黑白" "🔍 观察组 · 颜色" 🎞️ ✨ 🎬 👁️ ☀️ 🌑 🗿 \
        🌗 🔥 高频 低频 原图
    build --comp flat.comp --neutral-gray --sculpt --look bw --glow mist:ice --no-checks > flat.log 2>&1
    want 0 $? "modules added to the flat project"
    has "...the loose old sculpt counts: no second one" grep -m1 "SKIP\] --sculpt" flat.log
    order flat.comp "new folders next to the old loose layers, which stay where they were" "🔍 观察组 · 黑白" "🔍 观察组 · 颜色" \
        🎞️ 🌟 ✨ "🎬 调色 · 黑白" "🎬 调色 · 胶片" 👁️ 🌓 ☀️ 🌑 🗿 🌗 🔥 高频 低频 原图
    inside flat.comp "...the new neutral gray in a new light folder" 🌓 🩶
    build --comp flat.comp --delicious-skin manual --no-checks > /dev/null 2>&1
    order flat.comp "...a new skin folder on top of the old loose skin layers" 🧴 🌗 🔥 高频 低频 原图
    inside flat.comp "...with the new manual smoothing layer" 🧴 🔥
    build --comp flat.comp --delicious-skin auto --no-checks > remake.log 2>&1
    has "...an automatic one again remakes the old loose layer in place" grep -m1 "重做 · 保留蒙版" remake.log
    inside flat.comp "...so the folder still holds the manual one alone" 🧴 🔥
    build --comp flat.comp --delicious-skin auto --engine guided --no-checks > remake2.log 2>&1
    has "...a new engine on the next run: the same layer, renamed" grep -m1 "保边磨皮 · 导向滤波.*(重做" remake2.log
    build --comp flat.comp --freq-sep > again.log 2>&1; want 0 $? "frequency separation once more"
    has "...skipped: the loose pair is found" grep -m1 SKIP again.log
    local gray
    gray=$("$PY" -c "import json;print(next(l['imageFile'] for l in json.load(open('flat.comp/manifest.json'))['layers'] if l['name'].startswith('🌗')))")
    cp "flat.comp/images/$gray" gray.png
    build --comp flat.comp --refresh --no-checks > refresh.log 2>&1; want 0 $? "--refresh on it"
    cmp -s gray.png "flat.comp/images/$gray" && echo "  ok: ...the old gray dodge & burn, painted on, left as it is" \
        || echo "  BAD: the old gray layer was changed"
    build --init "$SMALL" --comp hidden.comp --sculpt --no-checks > /dev/null 2>&1
    "$PY" -c "
import json; p = 'hidden.comp/manifest.json'; m = json.load(open(p))
for l in m['layers']:
    if l.get('isGroup') and l['name'].startswith('🌓'): l['isVisible'] = False
json.dump(m, open(p, 'w'))"
    build --comp hidden.comp --neutral-gray --no-checks > hidden.log 2>&1; want 0 $? "a layer added to a hidden folder"
    has "...with a warning that it doesn't show" grep -m1 "is hidden in Compositor" hidden.log
    order flat.comp "...and the loose old layers are where they were" 🧴 🌗 🔥 高频 低频 原图
    validate flat.comp hidden.comp
}

suite_deep() {  # 16-bit (format 12) and HDR OpenEXR (format 13) layers, as Compositor 1.5 saves them
    build --init "$PHOTO" --comp p8.comp --no-checks > /dev/null 2>&1
    cp -R p8.comp d16.comp; cp -R p8.comp hdr.comp; cp -R p8.comp bright.comp; cp -R p8.comp old16.comp
    "$PY" - "$TOEXR" <<'EOF'
import json, os, subprocess, sys, numpy as np, cv2
from PIL import Image
for comp, version, kind in (("d16.comp", 12, "16"), ("hdr.comp", 13, "exr"), ("bright.comp", 13, "bright"),
                            ("old16.comp", 11, "16")):
    path = f"{comp}/manifest.json"; manifest = json.load(open(path)); manifest["version"] = version
    base = manifest["layers"][0]; png = f"{comp}/images/{base['imageFile']}"
    if kind == "16":
        rgba = np.asarray(Image.open(png).convert("RGBA")).astype(np.uint16) * 257
        cv2.imwrite(png, cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    else:
        exr = png[:-4] + ".exr"
        subprocess.run([sys.argv[1], png, exr] + (["1.6"] if kind == "bright" else []), check=True)
        os.remove(png); base["imageFile"] = os.path.basename(exr)
    json.dump(manifest, open(path, "w"), indent=2)
EOF
    for c in d16 hdr bright; do
        build --comp $c.comp --check > /dev/null 2>&1; want 0 $? "$c: --check"
        build --comp $c.comp --delicious-skin --eyes --render > $c.log 2>&1; want 0 $? "$c: smoothing, eyes and render"
    done
    "$PY" - <<'EOF'
import json, numpy as np, cv2
for comp, deep in (("d16", True), ("hdr", False)):
    m = json.load(open(f"{comp}.comp/manifest.json"))
    smooth = next(l for l in m["layers"] if l["name"].startswith("🔥"))
    img = cv2.imread(f"{comp}.comp/images/{smooth['imageFile']}", cv2.IMREAD_UNCHANGED)
    want = np.uint16 if deep else np.uint8
    print(f"  {'ok' if img.dtype == want and ('16 位' in smooth['name']) == deep else 'BAD'}: {comp}: the smoothing layer is "
          f"{'16-bit over a 16-bit photo' if deep else '8-bit over an EXR photo'} ({img.dtype})")
EOF
    "$PY" - "$S" <<'EOF'
import json, os, sys, numpy as np
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
photo = rb.Project.open("p8.comp").base_rgb().astype(int)
for comp in ("d16", "hdr"):
    difference = np.abs(rb.Project.open(f"{comp}.comp").base_rgb().astype(int) - photo).max()
    print(f"  {'ok' if difference <= 1 else 'BAD'}: {comp} base read as the 8-bit photo (max difference {difference})")
bright = rb.Project.open("bright.comp").base_rgb()
print(f"  {'ok' if bright.max() == 255 and (bright == 255).mean() > 0.05 else 'BAD'}: HDR highlights clipped to white "
      f"({(bright == 255).mean() * 100:.0f}% at white)")
for comp in ("d16", "hdr"):
    version = json.load(open(f"{comp}.comp/manifest.json"))["version"]
    print(f"  {'ok' if version == {'d16': 12, 'hdr': 13}[comp] else 'BAD'}: {comp} keeps format version {version}")
EOF
    "$MAKE16" "$PHOTO" p3.tif p3 6 && "$MAKE16" "$PHOTO" srgb.tif srgb || echo "  BAD: Make16"
    build --init p3.tif --comp t16.comp --freq-sep --no-checks > t16.log 2>&1; want 0 $? "--init from a 16-bit Display P3 TIFF, turned"
    has "...kept at 16 bits" grep -m1 "16-bit)" t16.log
    build --init p3.tif --comp t8.comp --8bit --no-checks > /dev/null 2>&1; want 0 $? "--8bit"
    build --comp t8.comp --8bit > /dev/null 2>&1; want 1 $? "--8bit without --init refused"
    "$PY" - "$PHOTO" <<'EOF'
import json, sys, numpy as np, cv2
from PIL import Image
def layers(comp):
    m = json.load(open(f"{comp}/manifest.json"))
    return m, {l["name"].split(" ")[0]: cv2.imread(f"{comp}/images/{l['imageFile']}", cv2.IMREAD_UNCHANGED) for l in m["layers"] if l.get("imageFile")}
m16, l16 = layers("t16.comp"); m8, l8 = layers("t8.comp")
photo = Image.open(sys.argv[1])
base16, base8 = l16["原图"], l8["原图"]
print(f"  {'ok' if m16['version'] == 12 and base16.dtype == np.uint16 else 'BAD'}: a 16-bit photo layer, format 12 "
      f"(v{m16['version']}, {base16.dtype})")
print(f"  {'ok' if m8['version'] == 11 and base8.dtype == np.uint8 else 'BAD'}: --8bit: an 8-bit one, format 11")
print(f"  {'ok' if base16.shape[:2] == (photo.width, photo.height) else 'BAD'}: turned upright by its EXIF orientation "
      f"({base16.shape[1]}x{base16.shape[0]})")
d = np.abs(base16[..., :3].astype(float) / 257 - base8[..., :3])
print(f"  {'ok' if d.mean() < 1 else 'BAD'}: colors as the 8-bit path converts them (mean {d.mean():.2f} levels)")
print(f"  {'ok' if len(np.unique(base16[..., 1])) > 1024 else 'BAD'}: precision kept ({len(np.unique(base16[..., 1]))} values in a channel)")
low, high = l16["低频"], l16["高频"]
rebuilt = low[..., :3].astype(float) + 2 * high[..., :3].astype(float) - 65535
error = np.abs(rebuilt - base16[..., :3]).max() / 257
print(f"  {'ok' if low.dtype == np.uint16 and error < 0.05 else 'BAD'}: a 16-bit frequency-separation pair that rebuilds "
      f"it (within {error:.3f} levels)")
EOF
    build --comp t16.comp --delicious-skin auto --sculpt --render > t16more.log 2>&1; want 0 $? "smoothing and sculpt on it, rendered"
    deep_smoothing() {  # deep_smoothing <label>: the smoothing layer of t16.comp is still 16-bit, with real precision
        "$PY" - "$1" <<'EOF'
import json, sys, numpy as np, cv2
m = json.load(open("t16.comp/manifest.json"))
smooth = next(l for l in m["layers"] if l["name"].startswith("🔥"))
img = cv2.imread(f"t16.comp/images/{smooth['imageFile']}", cv2.IMREAD_UNCHANGED)
mask = cv2.imread(f"t16.comp/images/{smooth['maskFile']}", cv2.IMREAD_UNCHANGED)
levels = len(np.unique(img[..., 1][mask > 200]))
print(f"  {'ok' if img.dtype == np.uint16 and '16 位' in smooth['name'] and levels > 300 else 'BAD'}: {sys.argv[1]}: a 16-bit "
      f"smoothing layer with more than 8-bit precision on the skin ({img.dtype}, {levels} values); {smooth['imageFile']} "
      f"opacity {smooth['opacity']}")
EOF
    }
    deep_smoothing "over the 16-bit pair"
    build --comp t16.comp --delicious-skin --skin-strength 0.6 --no-checks > t16light.log 2>&1; want 0 $? "made again lighter"
    has "...in place" grep -m1 "重做 · 保留蒙版" t16light.log
    deep_smoothing "after the re-make"
    build --comp t16.comp --refresh --no-checks > t16refresh.log 2>&1; want 0 $? "--refresh on it"
    deep_smoothing "after --refresh"
    build --comp old16.comp --check > old16.log 2>&1; want 1 $? "a 16-bit layer in a version-11 project refused"
    has "...naming the rule" grep -m1 "16-bit from format version 12" old16.log
    "$VALIDATE" old16.comp > /dev/null; want 1 $? "validator refuses it too"
    validate d16.comp hdr.comp bright.comp t16.comp t8.comp
}

suite_refresh() {  # --refresh: the snapshot layers recomputed in place, after manual work under them
    build --init "$PHOTO" --comp r.comp --8bit --freq-sep --delicious-skin auto --sculpt --eyes --dual-curves --no-checks > /dev/null 2>&1
    want 0 $? "a format-11 project with the layers --refresh recomputes (the pixel eyes), and ones it leaves alone"
    cp -R r.comp r0.comp
    build --comp r.comp --render r0.png > /dev/null 2>&1
    build --comp r.comp --refresh --render r1.png > r1.log 2>&1; want 0 $? "--refresh with nothing changed under them"
    [ "$(grep -c '(重算)' r1.log)" = 2 ] && echo "  ok: two layers recomputed (smoothing, eyes)" || echo "  BAD: $(grep -c '(重算)' r1.log) recomputed"
    "$PY" - <<'EOF'
import json, os, numpy as np
from PIL import Image
m0, m1 = (json.load(open(f"{c}/manifest.json")) for c in ("r0.comp", "r.comp"))
print("  ok: the manifest is as it was" if m0 == m1 else "  BAD: the manifest changed")
redone = ("🔥", "👁️")
kept = [l["maskFile"] for l in m1["layers"] if l.get("maskFile")] + \
       [l["imageFile"] for l in m1["layers"] if l.get("imageFile") and not l["name"].startswith(redone)]
changed = [f for f in kept if open(f"r0.comp/images/{f}", "rb").read() != open(f"r.comp/images/{f}", "rb").read()]
print("  ok: every mask and the other layers' pixels untouched (sculpt, curves, frequency pair)" if not changed else f"  BAD: changed {changed}")
a, b = (np.asarray(Image.open(f)).astype(int) for f in ("r0.png", "r1.png"))
d = np.abs(a - b)
ok = d.mean() < 0.05 and (d > 2).mean() < 0.0005
print(f"  {'ok' if ok else 'BAD'}: the picture stays (mean change {d.mean():.3f} levels, {(d > 2).mean() * 100:.3f}% over 2)")
left = [n for n in os.listdir("r.comp") if n.endswith((".new", ".old")) or n == ".retouch"]
print("  ok: no files left over" if not left else f"  BAD: left over {left}")
EOF
    build --init "$PHOTO" --comp rl.comp --delicious-skin --eyes --no-checks > /dev/null 2>&1
    cp -R rl.comp rl0.comp
    build --comp rl.comp --refresh --no-checks > rl.log 2>&1; want 0 $? "--refresh on a project with the live eye brilliance"
    [ "$(grep -c '(重算)' rl.log)" = 1 ] && echo "  ok: only the smoothing recomputed: the live Sharpen layer follows the picture by itself" || echo "  BAD: $(grep -c '(重算)' rl.log) recomputed"
    "$PY" -c "import json; a, b = (json.load(open(f'{c}/manifest.json')) for c in ('rl0.comp', 'rl.comp')); print('  ok: its manifest is as it was' if a == b else '  BAD: the manifest changed')"
    cp -R r0.comp e.comp
    "$PY" - "$S" <<'EOF'
# Manual work under the smoothing: the texture of a square of the high layer taken out, as a clone stamp would.
import json, os, sys, numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
p = rb.Project.open("e.comp")
high = next(l for l in p.layers if l["name"].startswith(rb.FS_HIGH))
x, y, w, h = (int(v) for v in p.faces()[0])
cx, cy, r = x + int(0.27 * w), y + int(0.62 * h), int(0.06 * w)
path = f"e.comp/images/{high['imageFile']}"
pixels = np.asarray(Image.open(path).convert("RGBA")).copy()
pixels[cy - r:cy + r, cx - r:cx + r, :3] = 128
Image.fromarray(pixels).save(path)
json.dump([cx, cy, r], open("square.json", "w"))
EOF
    build --comp e.comp --render e0.png > /dev/null 2>&1
    build --comp e.comp --refresh --render e1.png > /dev/null 2>&1; want 0 $? "--refresh after work on the high layer"
    "$PY" - <<'EOF'
import json, numpy as np, cv2
from PIL import Image
cx, cy, r = json.load(open("square.json"))
def texture(f):
    g = cv2.cvtColor(np.asarray(Image.open(f).convert("RGB")), cv2.COLOR_RGB2GRAY).astype(np.float32)
    return (g - cv2.GaussianBlur(g, (0, 0), 2.0))[cy - r + 3:cy + r - 3, cx - r + 3:cx + r - 3].std()
photo, hidden, shown = texture("r0.png"), texture("e0.png"), texture("e1.png")
print(f"  {'ok' if hidden > 0.7 * photo else 'BAD'}: before, the smoothing snapshot hides the edit (texture {photo:.2f} -> {hidden:.2f})")
print(f"  {'ok' if shown < 0.5 * hidden else 'BAD'}: after --refresh it shows (texture {shown:.2f})")
EOF
    cp -R r0.comp manual.comp
    "$PY" - <<'EOF'
# A manual smoothing mask brushed in by hand stays as it is through --refresh.
import json, numpy as np, cv2
from PIL import Image
m = json.load(open("manual.comp/manifest.json"))
layer = next(l for l in m["layers"] if l["name"].startswith("🔥"))
path = f"manual.comp/images/{layer['maskFile']}"
mask = np.zeros(np.asarray(Image.open(path)).shape[:2], np.uint8)
cv2.circle(mask, (mask.shape[1] // 2, mask.shape[0] // 2), min(mask.shape) // 5, 255, -1)
Image.fromarray(mask).save(path)
EOF
    local brushed
    brushed=$("$PY" -c "import json;print(next(l['maskFile'] for l in json.load(open('manual.comp/manifest.json'))['layers'] if l['name'].startswith('🔥')))")
    cp "manual.comp/images/$brushed" brushed.png
    build --comp manual.comp --refresh --no-checks > /dev/null 2>&1; want 0 $? "--refresh with a hand-brushed smoothing mask"
    cmp -s brushed.png "manual.comp/images/$brushed" && echo "  ok: ...the brushed mask is kept" || echo "  BAD: the mask changed"
    cp -R r0.comp broken.comp
    "$PY" -c "import json;p='broken.comp/manifest.json';m=json.load(open(p));m['layers'][0]['blendMode']='SoftLight';json.dump(m,open(p,'w'))"
    local before
    before=$(cat broken.comp/images/* | shasum)
    build --comp broken.comp --refresh > /dev/null 2>&1; want 1 $? "--refresh on a project Compositor would reject"
    [ "$before" = "$(cat broken.comp/images/* | shasum)" ] && [ -z "$(ls -a broken.comp | grep -E '\.(new|old|retouch)$')" ] \
        && echo "  ok: every image as it was, nothing left over" || echo "  BAD: images changed or files left over"
    build --init "$SMALL" --comp n.comp --glow --no-checks > /dev/null 2>&1
    build --comp n.comp --refresh > n.log 2>&1; want 0 $? "--refresh with nothing to recompute"
    has "...says so" grep -m1 "SKIP\] --refresh" n.log
    build --init "$PHOTO" --comp s.comp --delicious-skin --texture soft --strength 0.6 --no-checks > /dev/null 2>&1
    [ "$(stack s.comp | grep -c '力度 0.6')" = 1 ] && echo "  ok: a strength other than 1 in the layer name" \
        || echo "  BAD: strength not in the name"
    cp -R s.comp s0.comp
    build --comp s.comp --refresh > /dev/null 2>&1; want 0 $? "--refresh of a layer made at strength 0.6, texture soft"
    "$PY" - <<'EOF'
import json, numpy as np
from PIL import Image
m = json.load(open("s.comp/manifest.json"))
same = all(np.array_equal(*(np.asarray(Image.open(f"{c}/images/{l['imageFile']}")) for c in ("s0.comp", "s.comp")))
           for l in m["layers"] if l["name"].startswith("🔥"))
print("  ok: ...recomputed at that strength and texture (the same pixels)" if same else "  BAD: recomputed with other settings")
EOF
    cp -R r0.comp legacy.comp && flatten legacy.comp
    build --comp legacy.comp --refresh > legacy.log 2>&1; want 0 $? "--refresh on a project from before the folders"
    [ "$(grep -c '(重算)' legacy.log)" = 2 ] && echo "  ok: ...the same two recomputed" || echo "  BAD: $(grep -c '(重算)' legacy.log)"
    validate r.comp e.comp manual.comp n.comp s.comp legacy.comp
}

suite_vision() {  # Apple Vision, and every way of doing without it
    "$PY" - "$PHOTO" <<'EOF'
import sys, numpy as np, cv2
from PIL import Image
photo = np.asarray(Image.open(sys.argv[1]).convert("RGB")); h, w = photo.shape[:2]
Image.fromarray(cv2.warpAffine(photo, cv2.getRotationMatrix2D((w / 2, h / 2), 20, 1.0), (w, h), borderValue=(230, 230, 230))).save("tilted.jpg", quality=95)
small = cv2.resize(photo, (int(w * 0.8), int(h * 0.8)), interpolation=cv2.INTER_AREA)
pair = np.full((h, w + small.shape[1], 3), 230, np.uint8); pair[:, :w] = photo; pair[h - small.shape[0]:, w:] = small
Image.fromarray(pair).save("two.jpg", quality=95)
EOF
    "$PY" - "$S" "$PHOTO" <<'EOF'
# The face's "down" follows Vision's eye labels and the mouth, so a face turned past 90° keeps its axes.
import os, sys, numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
photo = np.asarray(Image.open(sys.argv[2]).convert("RGB"))
face = rb.landmark_faces(rb.detect_faces(photo))[0]
across, down, tilt = rb._face_axes(face.marks)
eyes = (face.marks["leftEye"].mean(axis=0) + face.marks["rightEye"].mean(axis=0)) / 2
mouth = face.marks["outerLips"].mean(axis=0)
print(f"  {'ok' if float((mouth - eyes) @ down) > 0 else 'BAD'}: the mouth lies down from the eyes")
turned = {name: 2 * eyes - points for name, points in face.marks.items()}  # the same face upside down, labels kept
across2, down2, tilt2 = rb._face_axes(turned)
print(f"  {'ok' if float(down @ down2) < -0.99 and abs(abs(tilt - tilt2) - 180) < 1 else 'BAD'}: upside down, 'down' turns "
      f"with the face (dot {float(down @ down2):.2f}, tilt {tilt:.0f} -> {tilt2:.0f})")
swapped = dict(face.marks, leftEye=face.marks["rightEye"], rightEye=face.marks["leftEye"])
_, down3, _ = rb._face_axes(swapped)
print(f"  {'ok' if float(down @ down3) > 0.99 else 'BAD'}: ...and the mouth settles it when the labels are the other way round")
rot = np.ascontiguousarray(photo[::-1, ::-1])
faces_r = rb.landmark_faces(rb.detect_faces(rot))
if faces_r:
    m1 = rb.sculpt_mask(photo.shape[:2], [face]) > 64  # painted at half strength
    m2 = (rb.sculpt_mask(rot.shape[:2], faces_r[:1]) > 64)[::-1, ::-1]
    iou = (m1 & m2).sum() / max(1, (m1 | m2).sum())
    print(f"  {'ok' if iou > 0.5 else 'BAD'}: the sculpt mask on the upside-down photo lands in the same place (IoU {iou:.2f})")
else:
    print("  (Vision finds no landmarks on the upside-down photo, so the sculpt check is skipped)")
EOF
    build --init "$PHOTO" --comp nv.comp --delicious-skin --sculpt --eyes --no-vision --no-checks > nv.log 2>&1
    want 0 $? "--no-vision"
    has "OpenCV faces without landmarks" grep -m1 "OpenCV, no landmarks" nv.log
    has "sculpt and eyes start with black masks" grep -m1 "黑蒙版涂白" nv.log
    build --init tilted.jpg --comp tilted.comp --preset dr5 --no-checks > tilted.log 2>&1; want 0 $? "tilted head"
    has "tilted: masks painted from landmarks" grep -m1 "预涂" tilted.log
    build --init two.jpg --comp two.comp --preset dr5 --no-checks > two.log 2>&1; want 0 $? "two people"
    has "two faces found" grep -m1 "Faces found: 2" two.log
    mkdir -p fakehome
    HOME="$WORK/fakehome" PATH="$(dirname "$(command -v "$PY")")" "$PY" "$S" --init "$PHOTO" --comp noswift.comp \
        --preset dr5 --no-checks > noswift.log 2>&1; want 0 $? "no swiftc, no cached helper"
    has "...falls back to OpenCV" grep -m1 "OpenCV, no landmarks" noswift.log
    build --init "$PHOTO" --comp np.comp --delicious-skin --no-parsing --no-checks > np.log 2>&1; want 0 $? "--no-parsing"
    [ "$(grep -c 'segmentation model' np.log)" = 0 ] && echo "  ok: no model used" || echo "  BAD: model used"
    mkdir -p shadow/ai_edge_litert && echo "raise ImportError('not installed')" > shadow/ai_edge_litert/__init__.py
    PYTHONPATH="$WORK/vision/shadow" build --init "$PHOTO" --comp nolite.comp --delicious-skin --no-checks > nolite.log 2>&1
    want 0 $? "without the TFLite runtime"
    has "...says how to install it, and tells hair by texture" grep -m1 "pip install ai-edge-litert" nolite.log
    { "$PY" - "$S" "$DATA/jsc2014e037863.jpg" <<'EOF'
# A person matte that leaves the face out must not be used: the skin mask stays as without one.
import sys, os, io, contextlib, numpy as np
sys.path.insert(0, os.path.dirname(sys.argv[1])); import retouch_builder as rb
p = rb.Project.create(os.path.abspath("guard.comp"), sys.argv[2], False); faces = p.faces()
p._people_small = np.zeros_like(p._people_small); p._people_small[:50, :50] = 255
err = io.StringIO()
with contextlib.redirect_stderr(err):
    people = p.people(); skin, _ = p.skin()
same = (skin == rb.skin_mask(p.base_rgb(), faces, None, p.parts())[0]).all()
print("  ok: a matte missing the face is ignored" if people is None and same and "leaves out the face" in err.getvalue()
      else "  BAD: a matte missing the face was used")
p.discard()
EOF
    } 2>&1 | grep -E "ok:|BAD|Traceback|Error"
    if [ -n "$PYTHON5" ]; then
        "$PYTHON5" "$S" --init "$PHOTO" --comp cv5.comp --preset dr5 --no-checks > cv5.log 2>&1; want 0 $? "OpenCV 5 with Vision"
        HOME="$WORK/fakehome" "$PYTHON5" "$S" --init "$PHOTO" --comp cv5nv.comp --preset dr5 --no-vision --no-checks > cv5nv.log 2>&1
        want 0 $? "OpenCV 5 without Vision (no Haar faces: masks start black)"
    fi
    validate nv.comp tilted.comp two.comp noswift.comp np.comp nolite.comp $( [ -n "$PYTHON5" ] && echo cv5.comp cv5nv.comp)
}

suite_glow() {  # glow styles and colors, placement, effects, and the same compositing Core Graphics does
    for spec in "" mist:gold mist:gold,rose mist:ice halation halation:natural dream "mist:#ff8800"; do
        name="g_$(echo "x$spec" | tr ':,#' '___')"
        if [ -z "$spec" ]; then build --init "$PHOTO" --comp "$name.comp" --glow > "$name.log" 2>&1
        else build --init "$PHOTO" --comp "$name.comp" --glow "$spec" > "$name.log" 2>&1; fi
        want 0 $? "--glow $spec"
    done
    [ -f g_x.checks/glow.jpg ] && echo "  ok: glow.jpg" || echo "  BAD: no glow.jpg"
    for spec in sparkle mist:sky "mist:#12345" dream:gold mist:gold,rose,ice; do
        build --init "$SMALL" --comp r.comp --glow "$spec" > /dev/null 2>&1; want 2 $? "--glow $spec refused"
    done
    build --init "$SMALL" --comp order.comp --grain --glow mist:gold --glow halation:red --no-checks > /dev/null 2>&1
    build --comp order.comp --glow mist:ice,teal --look film --no-checks > /dev/null 2>&1; want 0 $? "glows and a look added later"
    order order.comp "grain on top, the glow folder under it, the look under the glow" 🎞️ 🌟 🎬 原图
    inside order.comp "newer glow above older, in one folder" 🌟 "✨ 辉光 · 黑柔 · 双色" "✨ 辉光 · 胶片光晕" "✨ 辉光 · 黑柔 · 暖金"
    build --init "$PHOTO" --comp plainglow.comp --glow --no-checks > /dev/null 2>&1
    build --init "$PHOTO" --comp grainfirst.comp --grain --no-checks > /dev/null 2>&1
    build --comp grainfirst.comp --glow --no-checks > /dev/null 2>&1
    "$PY" - <<'EOF'
# A glow goes under the grain, so it is computed from what is under it: the same with or without grain above.
import json, numpy as np
from PIL import Image
def glow(comp):
    record = next(l for l in json.load(open(f"{comp}/manifest.json"))["layers"] if l["name"].startswith("✨"))
    return np.asarray(Image.open(f"{comp}/images/{record['imageFile']}"))
same = np.array_equal(glow("plainglow.comp"), glow("grainfirst.comp"))
print("  ok: the glow doesn't take in the grain above it" if same else "  BAD: the glow was computed through the grain")
EOF
    build --init "$SMALL" --comp eff.comp --glow mist:gold,rose --no-checks > /dev/null 2>&1
    for v in missing range enabled; do
        cp -R eff.comp "e_$v.comp"
        "$PY" - "$v" <<'EOF'
import json, sys
v = sys.argv[1]; p = f"e_{v}.comp/manifest.json"; m = json.load(open(p))
co = next(x for x in m["layers"] if x.get("effects"))["effects"]["colorOverlay"]
if v == "missing": del co["blue"]
if v == "range": co["opacity"] = 1.2
if v == "enabled": co["enabled"] = "yes"
json.dump(m, open(p, "w"))
EOF
        build --comp "e_$v.comp" --check > /dev/null 2>&1; want 1 $? "--check rejects effects: $v"
        "$VALIDATE" "e_$v.comp" > /dev/null; want 1 $? "validator rejects effects: $v"
    done
    rm -rf e_*.comp
    mkdir -p cg && for spec in "" mist:gold mist:gold,rose mist:ice halation; do
        build --init "$PHOTO" --comp "cg/c$(echo "x$spec" | tr ':,#' '___').comp" --glow "${spec:-mist}" --no-checks > /dev/null 2>&1
    done
    build --init "$PHOTO" --comp cg/stacked.comp --glow mist:gold --glow halation:red --no-checks > /dev/null 2>&1
    "$PY" "$HERE/bench.py" cg cg "$CG"
    validate g_*.comp order.comp eff.comp cg/*.comp
}

suite_render() {  # adjustment layers as Compositor computes them, color grades and the flattened render
    echo "  -- the ports of Compositor's adjustments, pixel for pixel"
    "$PY" "$HERE/bench.py" adjust "$ADJUST" 2>&1 | grep -E "WORST|BAD|Traceback"
    build --init "$PHOTO" --comp full.comp --preset dr5 --neutral-gray --look film --glow mist:gold --no-checks > /dev/null 2>&1
    local before
    before=$(shasum full.comp/manifest.json)
    build --comp full.comp --render > render.log 2>&1; want 0 $? "--render alone"
    [ "$before" = "$(shasum full.comp/manifest.json)" ] && echo "  ok: the project is left as it was" || echo "  BAD: --render changed the project"
    [ -s full.render.jpg ] && echo "  ok: full.render.jpg" || echo "  BAD: nothing rendered"
    [ "$(grep -c WARN render.log)" = 0 ] && echo "  ok: everything in it reproduced" || { echo "  BAD: render warnings"; grep WARN render.log; }
    build --comp full.comp --render out.png > /dev/null 2>&1; want 0 $? "--render to a PNG"
    "$PY" -c "from PIL import Image; im = Image.open('out.png'); print('  ok: out.png', im.size) if im.size == Image.open('$PHOTO').size else print('  BAD: size')"
    for look in airy film cool-fair creamy hk bw; do
        build --init "$SMALL" --comp "look_$look.comp" --look $look > "look_$look.log" 2>&1; want 0 $? "--look $look"
        [ -f "look_$look.checks/look.jpg" ] || echo "  BAD: no look.jpg for $look"
    done
    build --init "$SMALL" --comp lookstrength.comp --look film --strength 0.6 --no-checks > /dev/null 2>&1
    has "--strength sets the grade folder's opacity" grep -m1 '"opacity": 0.6' lookstrength.comp/manifest.json
    order look_film.comp "a grade sits above the photo" 🎬 原图
    "$PY" -c "
import json; p = 'look_film.comp/manifest.json'; m = json.load(open(p))
blur = dict(m['layers'][-1]); blur.update(id='0C5E7A91-3B2D-4F6A-8E1C-9D0B7A6F5E4D', name='motion', parentID=None)
blur['adjustment'] = dict(blur['adjustment'], kind='Motion Blur'); blur.pop('parentID'); m['layers'].append(blur)
json.dump(m, open(p, 'w'))"
    build --comp look_film.comp --render > motion.log 2>&1; want 0 $? "a render with a Motion Blur layer"
    has "...says it is left out" grep -m1 "isn't reproduced" motion.log
    validate full.comp look_*.comp lookstrength.comp
}

suite_benches() {  # measured against the floors recorded when the modules were tuned
    for bench in skin smoothing light; do
        echo "  -- $bench"
        "$PY" "$HERE/bench.py" $bench 2>&1 | grep -v XNNPACK
    done
}

suite_light() {  # --ai-light: a first pass of dodge & burn, painted on a gray layer or on the two curves' masks
    build --init "$PHOTO" --comp l.comp --ai-light look > look.log 2>&1; want 0 $? "--ai-light look on a new project"
    has "...says what it measured, and what auto would make of it" grep -m1 "measured only, nothing added: skin patches differ.*auto would take" look.log
    [ -s l.checks/light_look.jpg ] && echo "  ok: ...and writes the picture to judge the needs by" || echo "  BAD: no light_look.jpg"
    cp l.comp/manifest.json look.json
    build --comp l.comp --ai-light look --no-checks > /dev/null 2>&1; want 0 $? "a look at an existing project"
    cmp -s l.comp/manifest.json look.json && echo "  ok: ...leaves it as it was" || echo "  BAD: a look changed the project"
    build --comp l.comp --ai-light skin=clear,circles=clear,folds=clear --render l.render.png > add.log 2>&1
    want 0 $? "the pass at full needs, on gray"
    has "...reports what was measured and chosen" grep -m1 "AI 光影: skin patches differ.*skin=clear" add.log
    has "...and how close the layer lands" grep -m1 "lands within 0.[0-4] levels" add.log
    for picture in light.jpg light_bw.jpg light_map.jpg; do
        [ -s "l.checks/$picture" ] && echo "  ok: ...check image $picture" || echo "  BAD: no $picture"
    done
    validate l.comp
    cp -R l.comp lf.comp
    build --comp l.comp --ai-light > again.log 2>&1; want 0 $? "run again"
    has "...the pass is not painted twice" grep -m1 "\[SKIP\] --ai-light: the project has its AI 光影 pass" again.log
    cp l.comp/manifest.json before.json
    build --comp l.comp --ai-light skin=some,circles=some,folds=none --ai-light-redo --no-checks > redo.log 2>&1
    want 0 $? "--ai-light-redo with other needs"
    build --comp l.comp --ai-light --ai-light-redo --ai-light-on curves --no-checks > kind.log 2>&1
    want 1 $? "a redo on the other kind of layer is refused"
    build --comp l.comp --ai-light skin=none,circles=none,folds=none --ai-light-redo --no-checks > none.log 2>&1
    want 0 $? "every need none"
    has "...paints nothing and says so" grep -m1 "\[SKIP\] --ai-light: every need is none" none.log
    build --comp l.comp --neutral-gray --no-checks > gray.log 2>&1
    has "...the pass is the neutral gray layer to go on painting on" grep -m1 "\[SKIP\] --neutral-gray" gray.log
    validate l.comp
    build --init "$PHOTO" --comp k.comp --ai-light skin=clear,circles=clear,folds=clear --ai-light-on curves \
        --render k.render.png --no-checks > curves.log 2>&1; want 0 $? "the pass on the two curves"
    validate k.comp
    build --comp k.comp --dual-curves --no-checks > pair.log 2>&1
    has "...its curves are the pair to go on brushing on" grep -m1 "\[SKIP\] --dual-curves" pair.log
    build --init "$PHOTO" --comp d.comp --preset daily --ai-light --no-checks > daily.log 2>&1; want 0 $? "with the daily preset"
    validate d.comp
    build --comp l.comp --ai-light bogus > /dev/null 2>&1; want 2 $? "a need that is no need is refused"
    "$PY" -c "from PIL import Image; Image.new('RGB', (600, 600), (120, 130, 140)).save('flat.png')"
    build --init flat.png --comp f.comp --ai-light --no-checks > flat.log 2>&1; want 0 $? "a photo without a face"
    has "...is left for the hand" grep -m1 "\[SKIP\] --ai-light: no face found" flat.log
    "$PY" "$HERE/bench.py" lightcheck "$PHOTO" 2>&1 | grep -v XNNPACK
}

suite_heal() {  # planted spots of known size and place, healed by the smoothing before it smooths
    for strength in faint strong dense; do
        "$PY" "$HERE/heal_eval.py" make "$PHOTO" "$strength.jpg" "$strength.json" $strength > /dev/null
        build --init "$strength.jpg" --comp "$strength.comp" --delicious-skin --no-checks > "$strength.log" 2>&1
        "$PY" "$HERE/heal_eval.py" score "$strength.comp" "$PHOTO" "$strength.jpg" "$strength.json" > "$strength.txt" 2>&1
        "$PY" - "$strength" "$strength.txt" "$strength.log" <<'EOF'
import re, sys
strength, text, log = sys.argv[1], open(sys.argv[2]).read(), open(sys.argv[3]).read()
found, planted = map(int, re.search(r"recall (\d+)/(\d+)", text).groups())
background = int(re.search(r"background spots healed (\d+)/", text).group(1))
outside = int(re.search(r"pixels changed outside the heal mask: (\d+)", text).group(1))
floor = {"strong": 12, "faint": 9, "dense": 25}[strength]  # dense: the inner-face-only search found 12
ok = found >= floor and background == 0 and outside == 0
print(f"  {'ok' if ok else 'BAD'}: {strength} spots found {found}/{planted} (floor {floor}), background healed {background}, "
      f"pixels changed outside the mask {outside}")
named = re.search(r"祛痘 (\d+) 处", log)
print(f"  {'ok' if named and int(named.group(1)) >= floor else 'BAD'}: ...and the smoothing layer's name says so "
      f"({named.group(0) if named else 'nothing'})")
EOF
    done
    has "acne-prone skin is named in the layer" grep -m1 "保边磨皮 · 保形.*祛痘 [0-9]* 处(痘痘肌)" dense.log
    validate faint.comp strong.comp dense.comp
}

suite_validator() {  # the validator itself still rejects what Compositor rejects
    build --init "$SMALL" --comp v.comp --glow --no-checks > /dev/null 2>&1
    "$VALIDATE" v.comp > /dev/null; want 0 $? "a good project passes"
    cp -R v.comp vt.comp && "$PY" -c "import json;p='vt.comp/manifest.json';m=json.load(open(p));m['layers'][0]['transform']['origin']={'x':0,'y':0};json.dump(m,open(p,'w'))"
    "$VALIDATE" vt.comp > /dev/null; want 1 $? "an object transform fails"
    cp -R v.comp vm.comp && "$PY" -c "import json;p='vm.comp/manifest.json';m=json.load(open(p));l=next(x for x in m['layers'] if x.get('maskFile'));l.pop('maskFile');json.dump(m,open(p,'w'))"
    "$VALIDATE" vm.comp > /dev/null; want 1 $? "maskEnabled without maskFile fails"
    build --init "$SMALL" --comp vl.comp --sculpt --eyes --no-checks > /dev/null 2>&1
    "$VALIDATE" vl.comp > /dev/null; want 0 $? "a project with live Tonal Contrast and Sharpen layers passes"
    cp -R vl.comp vr.comp && "$PY" -c "import json;p='vr.comp/manifest.json';m=json.load(open(p));l=next(x for x in m['layers'] if (x.get('adjustment') or {}).get('kind')=='Tonal Contrast');l['adjustment']['filterLayerSettings']['tonal']['amount']=150;json.dump(m,open(p,'w'))"
    "$VALIDATE" vr.comp > /dev/null; want 1 $? "a Tonal Contrast amount over 100 fails"
    build --comp vr.comp --check > /dev/null 2>&1; want 1 $? "...and --check refuses it too"
    cp -R vl.comp vs.comp && "$PY" -c "import json;p='vs.comp/manifest.json';m=json.load(open(p));l=next(x for x in m['layers'] if (x.get('adjustment') or {}).get('kind')=='Sharpen');l['adjustment']['filterLayerSettings'].pop('bloomAmount');json.dump(m,open(p,'w'))"
    "$VALIDATE" vs.comp > /dev/null; want 1 $? "a shared filter field left out fails"
    build --comp vs.comp --check > /dev/null 2>&1; want 1 $? "...and --check refuses it too"
    # Compositor 1.6 saves format 21, and its Reduce Noise layers (not reproduced here) must not stop a run
    cp -R vl.comp vn.comp && reduce_noise vn.comp 21
    "$VALIDATE" vn.comp > /dev/null; want 0 $? "a format-21 project with a Reduce Noise layer passes"
    build --comp vn.comp --grain --glow --no-checks > vn.log 2>&1; want 0 $? "...and takes new layers"
    lacks "...with no warning that the format is newer than the skill knows" grep -m1 "newer than" vn.log
    has "...saying the Reduce Noise layer is left out of computed layers" grep -m1 "Reduce Noise adjustment .* isn't reproduced" vn.log
    "$PY" -c "import json; m = json.load(open('vn.comp/manifest.json')); k = [(l.get('adjustment') or {}).get('kind') for l in m['layers']]
print('  ok: ...still format 21 with its Reduce Noise layer' if m['version'] == 21 and 'Reduce Noise' in k else f'  BAD: version {m[\"version\"]}, kinds {k}')"
    "$VALIDATE" vn.comp > /dev/null; want 0 $? "...and still passes"
    cp -R vl.comp vo.comp && reduce_noise vo.comp 20
    "$VALIDATE" vo.comp > /dev/null; want 1 $? "a Reduce Noise layer in a format-20 project fails"
    build --comp vo.comp --check > /dev/null 2>&1; want 1 $? "...and --check refuses it too"
    cp -R vl.comp vb.comp && reduce_noise vb.comp 21 150
    "$VALIDATE" vb.comp > /dev/null; want 1 $? "a Reduce Noise strength over 100 fails"
    build --comp vb.comp --check > /dev/null 2>&1; want 1 $? "...and --check refuses it too"
}

suite_timing() {  # on request: a 24-megapixel photo through the dr5 preset with the neutral gray
    [ -s "$DATA/collins_24mp.jpg" ] || "$PY" -c "
from PIL import Image; Image.open('$PHOTO').resize((4400, 5500), Image.LANCZOS).save('$DATA/collins_24mp.jpg', quality=92)"
    /usr/bin/time -p "$PY" "$S" --init "$DATA/collins_24mp.jpg" --comp big.comp --preset dr5 --neutral-gray --no-checks \
        > big.log 2> big.err; want 0 $? "24 MP dr5 + neutral gray"
    echo "  $(grep real big.err)"
    validate big.comp
}

# ---- run ----

SUITES=("$@")
[ ${#SUITES[@]} = 0 ] && SUITES=(format skin-stack folders deep refresh vision glow light render benches heal validator)
echo "compositor-retouch tests: $PY ($("$PY" --version 2>&1)), segmentation model: $MODEL, work: $WORK"
failed=()
for suite in "${SUITES[@]}"; do
    fn="suite_${suite//-/_}"
    if ! declare -f "$fn" > /dev/null; then echo "unknown suite: $suite"; failed+=("$suite"); continue; fi
    echo "== $suite"
    mkdir -p "$WORK/$suite" && cd "$WORK/$suite" || exit 2
    started=$(date +%s)
    $fn 2>&1 | tee "$WORK/$suite.log"
    if grep -qE "BAD|^FAIL|Traceback" "$WORK/$suite.log"; then failed+=("$suite"); fi
    echo "   ($(( $(date +%s) - started ))s)"
done
echo
if [ ${#failed[@]} = 0 ]; then echo "ALL PASSED (${SUITES[*]})"; else echo "FAILED: ${failed[*]}"; exit 1; fi
