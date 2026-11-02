#!/bin/bash
# Builds Compositor 工具.app from Sources/ with swiftc (no Xcode project), signs it ad hoc and packs it into a disk image,
# Compositor 工具.dmg in the skill folder: opened, it shows the usual installer window (drag the app onto 应用程序).
# Nothing is installed, opened or launched here, and nothing appears in Finder.
#
#   app/build.sh                 build and write <skill folder>/Compositor 工具.dmg
#   app/build.sh <file.dmg>      write the disk image there instead
#
# The app runs the skill's own scripts/retouch_builder.py (its path is written into Info.plist, so edits to the skill
# take effect without rebuilding); a copy bundled inside the app is the fallback if the skill folder moves.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL="$(cd "$HERE/.." && pwd)"
NAME="Compositor 工具"
EXECUTABLE="RetouchStudio"
VERSION="1.0"
DMG="${1:-$SKILL/$NAME.dmg}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/retouch-studio-build.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
STAGE="$WORK/$NAME"
OUT="$STAGE/$NAME.app"

mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources/skill/scripts"
echo "== compiling"
swiftc -O -swift-version 5 -target arm64-apple-macos26.0 -parse-as-library \
    -o "$OUT/Contents/MacOS/$EXECUTABLE" "$HERE"/Sources/*.swift
cp "$SKILL/scripts/retouch_builder.py" "$SKILL/scripts/face_landmarks.swift" "$OUT/Contents/Resources/skill/scripts/"
# the PhotoCraft tab's script, beside it as in the skill folder (it finds retouch_builder.py at ../../scripts)
mkdir -p "$OUT/Contents/Resources/skill/photocraft-retouch/scripts"
cp "$SKILL/photocraft-retouch/scripts/pcraft_retouch.py" "$SKILL/photocraft-retouch/scripts/photocraft.py" \
    "$OUT/Contents/Resources/skill/photocraft-retouch/scripts/"

# shipped so the first photo needn't wait for them (and no compiler is needed on the Mac that runs the app):
# the Apple Vision face helper, built here, and the segmentation model (MediaPipe, Apache 2.0)
echo "== face helper and segmentation model"
swiftc -O -swift-version 5 -target arm64-apple-macos12.0 -o "$OUT/Contents/MacOS/face_landmarks" "$SKILL/scripts/face_landmarks.swift"
MODEL="$HOME/Library/Caches/compositor-retouch/selfie_multiclass_256x256.tflite"
MODEL_SHA=$(sed -n 's/^PARSING_MODEL_SHA256 = "\(.*\)"/\1/p' "$SKILL/scripts/retouch_builder.py")
if [ "$(shasum -a 256 "$MODEL" 2> /dev/null | cut -d' ' -f1)" != "$MODEL_SHA" ]; then
    MODEL_URL=$(cd "$SKILL/scripts" && python3 -c "import ast,sys;t=ast.parse(open('retouch_builder.py').read())
print(next(ast.literal_eval(n.value) for n in t.body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')=='PARSING_MODEL_URL'))")
    mkdir -p "$(dirname "$MODEL")" && curl -sS -m 300 -f -o "$MODEL.tmp" "$MODEL_URL" && mv "$MODEL.tmp" "$MODEL"
    [ "$(shasum -a 256 "$MODEL" | cut -d' ' -f1)" = "$MODEL_SHA" ] || { echo "the model doesn't match its checksum"; exit 1; }
fi
mkdir -p "$OUT/Contents/Resources/models"
cp "$MODEL" "$OUT/Contents/Resources/models/"
cat > "$OUT/Contents/Resources/models/NOTICE.txt" <<'NOTICE'
selfie_multiclass_256x256.tflite: MediaPipe selfie multiclass segmentation model, by Google, under the Apache
License 2.0 (https://www.apache.org/licenses/LICENSE-2.0). Unmodified; used to tell hair and clothes from skin.
NOTICE
cp "$HERE/Resources/AppIcon.icns" "$OUT/Contents/Resources/"

xml() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; echo "${s//>/&gt;}"; }
cat > "$OUT/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>             <string>local.compositor-retouch.studio</string>
    <key>CFBundleName</key>                   <string>$NAME</string>
    <key>CFBundleDisplayName</key>            <string>$NAME</string>
    <key>CFBundleExecutable</key>             <string>$EXECUTABLE</string>
    <key>CFBundleIconFile</key>               <string>AppIcon</string>
    <key>CFBundlePackageType</key>            <string>APPL</string>
    <key>CFBundleShortVersionString</key>     <string>$VERSION</string>
    <key>CFBundleVersion</key>                <string>$(date +%Y%m%d%H%M)</string>
    <key>CFBundleDevelopmentRegion</key>      <string>zh_CN</string>
    <key>LSMinimumSystemVersion</key>         <string>26.0</string>
    <key>LSApplicationCategoryType</key>      <string>public.app-category.photography</string>
    <key>NSHighResolutionCapable</key>        <true/>
    <key>NSSupportsAutomaticTermination</key> <false/>
    <key>RetouchSkillScripts</key>            <string>$(xml "$SKILL/scripts")</string>
    <key>UTImportedTypeDeclarations</key>
    <array>
        <dict>
            <key>UTTypeIdentifier</key>       <string>com.compositor.project</string>
            <key>UTTypeDescription</key>      <string>Compositor Project</string>
            <key>UTTypeConformsTo</key>       <array><string>com.apple.package</string><string>public.composite-content</string></array>
            <key>UTTypeTagSpecification</key> <dict><key>public.filename-extension</key><array><string>comp</string></array></dict>
        </dict>
    </array>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key>       <string>Photo</string>
            <key>CFBundleTypeRole</key>       <string>Viewer</string>
            <key>LSHandlerRank</key>          <string>Alternate</string>
            <key>LSItemContentTypes</key>
            <array><string>public.jpeg</string><string>public.png</string><string>public.tiff</string>
                   <string>public.heic</string><string>public.heif</string></array>
        </dict>
        <dict>
            <key>CFBundleTypeName</key>       <string>Compositor Project</string>
            <key>CFBundleTypeRole</key>       <string>Editor</string>
            <key>LSHandlerRank</key>          <string>Alternate</string>
            <key>LSTypeIsPackage</key>        <true/>
            <key>LSItemContentTypes</key>     <array><string>com.compositor.project</string></array>
        </dict>
        <dict>
            <key>CFBundleTypeName</key>       <string>PhotoCraft Document</string>
            <key>CFBundleTypeRole</key>       <string>Editor</string>
            <key>LSHandlerRank</key>          <string>Alternate</string>
            <key>CFBundleTypeExtensions</key> <array><string>pcraft</string></array>
        </dict>
    </array>
</dict>
</plist>
PLIST
plutil -lint -s "$OUT/Contents/Info.plist"
printf 'APPL????' > "$OUT/Contents/PkgInfo"

echo "== signing"
# macOS remembers the Accessibility permission by the app's designated requirement. A plain ad-hoc signature's is
# its cdhash, new with every build, so each rebuild asked for the permission again. Now the requirement is fixed:
# the bundle identifier, written in explicitly. A local self-signed certificate is also prepared here (headless,
# in its own keychain); codesign accepts it only once it is trusted, which takes one command the user runs
# themselves (it asks for authorization, so this script never runs it). When it is trusted, the app is signed
# with it; until then ad hoc with the fixed requirement, which the certificate's own requirement still satisfies.
SIGN_DIR="$HOME/Library/Application Support/compositor-retouch/signing"
KEYCHAIN="$SIGN_DIR/dev.keychain-db"
CERT="$SIGN_DIR/cert.pem"
IDENTITY="Compositor 工具 (local)"
BUNDLE_ID="local.compositor-retouch.studio"
if [ ! -f "$KEYCHAIN" ]; then
    mkdir -p "$SIGN_DIR"
    ( cd "$SIGN_DIR" \
      && openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 3650 -nodes -subj "/CN=$IDENTITY" \
            -addext "keyUsage=critical,digitalSignature" -addext "extendedKeyUsage=critical,codeSigning" \
            -addext "basicConstraints=critical,CA:false" 2> /dev/null \
      && openssl pkcs12 -export -legacy -out identity.p12 -inkey key.pem -in cert.pem -passout pass:compositor \
            -name "$IDENTITY" 2> /dev/null \
      && security create-keychain -p compositor "$KEYCHAIN" && security set-keychain-settings "$KEYCHAIN" \
      && security import identity.p12 -k "$KEYCHAIN" -P compositor -T /usr/bin/codesign -f pkcs12 > /dev/null \
      && security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k compositor "$KEYCHAIN" > /dev/null 2>&1 \
      && rm -f key.pem identity.p12 ) || echo "   (could not prepare the local certificate; signing ad hoc)"
fi
codesign --force --sign - --timestamp=none "$OUT/Contents/MacOS/face_landmarks"
if [ -f "$KEYCHAIN" ] && security unlock-keychain -p compositor "$KEYCHAIN" 2> /dev/null \
        && security find-identity -v -p codesigning "$KEYCHAIN" 2> /dev/null | grep -q "$IDENTITY"; then
    codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --timestamp=none --identifier "$BUNDLE_ID" "$OUT"
    echo "   signed with the local certificate"
else
    codesign --force --sign - --timestamp=none --identifier "$BUNDLE_ID" \
        --requirements "=designated => identifier \"$BUNDLE_ID\"" "$OUT"
    echo "   signed ad hoc with a fixed designated requirement (identifier $BUNDLE_ID)"
    echo "   to sign with the local certificate instead, trust it once (this asks for your password):"
    echo "     security add-trusted-cert -r trustRoot -p codeSign -k \"$KEYCHAIN\" \"$CERT\""
fi
codesign --verify --strict "$OUT"
echo "   $(codesign -d -r- "$OUT" 2>&1 | grep designated)"
echo "built $NAME.app ($(du -sh "$OUT" | cut -f1))"

echo "== disk image"
# dmgbuild lays out the window (background, icon places, no toolbar) by writing the volume's .DS_Store itself and
# mounts with -nobrowse: no Finder window and no AppleScript, unlike the usual tools. It lives in its own environment
# in the cache, installed from the internet the first time.
TOOLS="$HOME/Library/Caches/compositor-retouch/dmgbuild"
if ! "$TOOLS/bin/python3" -c "import dmgbuild" 2> /dev/null; then
    PYTHON="$(command -v /opt/homebrew/bin/python3 || command -v python3)"
    rm -rf "$TOOLS" && "$PYTHON" -m venv "$TOOLS" \
        && "$TOOLS/bin/python3" -m pip install -q --disable-pip-version-check dmgbuild \
        || { echo "could not install dmgbuild (network?)"; exit 1; }
fi
cat > "$WORK/dmg.py" <<'SETTINGS'
import os.path
app = defines["app"]
name = os.path.basename(app)
format = "UDZO"
filesystem = "HFS+"
files = [app]
symlinks = {"应用程序": "/Applications"}
hide_extension = [name]
icon = defines["icon"]
background = defines["background"]
window_rect = ((200, 140), (640, 400))
default_view = "icon-view"
show_status_bar = show_tab_view = show_toolbar = show_pathbar = show_sidebar = show_icon_preview = False
icon_size = 128
text_size = 13
icon_locations = {name: (170, 200), "应用程序": (470, 200)}  # Tools/Snapshot.swift DMGLayout
SETTINGS
"$TOOLS/bin/python3" -m dmgbuild -s "$WORK/dmg.py" -D app="$OUT" -D icon="$HERE/Resources/AppIcon.icns" \
    -D background="$HERE/Resources/dmg-background.tiff" "$NAME" "$WORK/$NAME.dmg" > "$WORK/dmgbuild.log" 2>&1 \
    || { cat "$WORK/dmgbuild.log"; exit 1; }
hdiutil verify -quiet "$WORK/$NAME.dmg"

# the disk image file itself shows the app's icon
cat > "$WORK/icon.swift" <<'SWIFT'
import AppKit
let args = CommandLine.arguments
exit(NSWorkspace.shared.setIcon(NSImage(contentsOfFile: args[1]), forFile: args[2]) ? 0 : 1)
SWIFT
swiftc -o "$WORK/seticon" "$WORK/icon.swift" 2> /dev/null && "$WORK/seticon" "$HERE/Resources/AppIcon.png" "$WORK/$NAME.dmg" \
    || echo "(the disk image keeps the standard icon)"

mkdir -p "$(dirname "$DMG")"
rm -f "$DMG"
mv "$WORK/$NAME.dmg" "$DMG"
echo "wrote $DMG ($(du -h "$DMG" | cut -f1))"
