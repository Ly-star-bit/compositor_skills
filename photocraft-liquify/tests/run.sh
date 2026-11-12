#!/bin/bash
# Tests for photocraft-liquify, command line only (photocraft-cli mcp has no window).
#   tests/run.sh                    every suite
#   tests/run.sh plan tone          some suites (pin plan grid liquify captions tone guards)
#   PYTHON=/path/to/python3 tests/run.sh
# Without PYTHON, scripts/python.sh picks (or once makes) a Python with numpy and OpenCV.
# The test photo is the NASA portrait compositor-retouch's own tests use, from their cache when it is there.
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
PYTHON="${PYTHON:-$(bash "$HERE/../scripts/python.sh")}" || exit 2
CACHE="$HOME/Library/Caches/photocraft-liquify/tests"
PHOTO="$HOME/Library/Caches/compositor-retouch/tests/data/collins_orig.jpg"
mkdir -p "$CACHE"
if [ ! -f "$PHOTO" ]; then
    PHOTO="$HOME/Library/Caches/photocraft-retouch/tests/collins_orig.jpg"
fi
if [ ! -f "$PHOTO" ]; then
    PHOTO="$CACHE/collins_orig.jpg"
    [ -f "$PHOTO" ] || curl -fsSL --max-time 120 "https://images-assets.nasa.gov/image/S99-00858/S99-00858~orig.jpg" -o "$PHOTO" || exit 2
fi
WORK="$(mktemp -d "$CACHE/work.XXXXXX")"
"$PYTHON" -I "$HERE/check.py" "$WORK" "$PHOTO" "$@"
status=$?
[ "${KEEP:-0}" = 1 ] && echo "kept: $WORK" || rm -rf "$WORK"
exit $status
