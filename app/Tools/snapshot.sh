#!/bin/bash
# Pictures of the app's screens, made offscreen (nothing is shown, the app is never launched), for reviewing the
# design; and the app icon (Resources/AppIcon.icns) from the same drawing code.
#
#   app/Tools/snapshot.sh screens <out folder> <photo folder> [<e2e folder with finished projects>]
#   app/Tools/snapshot.sh icon
#   app/Tools/snapshot.sh dmg [<preview.png>]   Resources/dmg-background.tiff, the installer window's background
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/.."
BIN="$(mktemp -d "${TMPDIR:-/tmp}/retouch-studio-snapshot.XXXXXX")"
trap 'rm -rf "$BIN"' EXIT
SOURCES=()
for source in "$APP"/Sources/*.swift; do [ "$(basename "$source")" = App.swift ] || SOURCES+=("$source"); done
swiftc -swift-version 5 -target arm64-apple-macos26.0 -parse-as-library -o "$BIN/Snapshot" "${SOURCES[@]}" "$HERE/Snapshot.swift"

case "${1:-}" in
screens)
    shift
    "$BIN/Snapshot" screens "$@"
    ;;
icon)
    "$BIN/Snapshot" icon "$BIN/icon.png"
    ICONSET="$BIN/AppIcon.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 128 256 512; do
        sips -z $size $size "$BIN/icon.png" --out "$ICONSET/icon_${size}x${size}.png" > /dev/null
        sips -z $((size * 2)) $((size * 2)) "$BIN/icon.png" --out "$ICONSET/icon_${size}x${size}@2x.png" > /dev/null
    done
    mkdir -p "$APP/Resources"
    iconutil -c icns "$ICONSET" -o "$APP/Resources/AppIcon.icns"
    cp "$BIN/icon.png" "$APP/Resources/AppIcon.png"
    echo "wrote $APP/Resources/AppIcon.icns"
    ;;
dmg)
    "$BIN/Snapshot" dmg "$BIN/background.png"
    tiffutil -cathidpicheck "$BIN/background.png" "$BIN/background@2x.png" -out "$APP/Resources/dmg-background.tiff" 2> /dev/null
    echo "wrote $APP/Resources/dmg-background.tiff"
    [ -n "${2:-}" ] && "$BIN/Snapshot" dmg-preview "$BIN/background@2x.png" "$2"
    ;;
*)
    echo "usage: $0 screens <out> <photos> [<e2e folder>] | $0 icon | $0 dmg [<preview.png>]"; exit 2
    ;;
esac
