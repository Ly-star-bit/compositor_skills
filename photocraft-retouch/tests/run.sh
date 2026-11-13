#!/bin/bash
# Tests for photocraft-retouch, command line only (photocraft-cli serve has no window).
#   tests/run.sh                    every suite
#   tests/run.sh looks glow         some suites (dr5 looks glow append light refresh errors)
#   PYTHON=/path/to/python3 tests/run.sh
# The test photo is the NASA portrait compositor-retouch's own tests use, from their cache when it is there.
set -u
HERE="$(cd "$(dirname "$0")" && pwd -P)"
PYTHON="${PYTHON:-python3}"
CACHE="$HOME/Library/Caches/photocraft-retouch/tests"
PHOTO="$HOME/Library/Caches/compositor-retouch/tests/data/collins_orig.jpg"
mkdir -p "$CACHE"
if [ ! -f "$PHOTO" ]; then
    PHOTO="$CACHE/collins_orig.jpg"
    [ -f "$PHOTO" ] || curl -fsSL --max-time 120 "https://images-assets.nasa.gov/image/S99-00858/S99-00858~orig.jpg" -o "$PHOTO" || exit 2
fi
WORK="$(mktemp -d "$CACHE/work.XXXXXX")"
"$PYTHON" -I "$HERE/check.py" "$WORK" "$PHOTO" "$@"
status=$?
[ "${KEEP:-0}" = 1 ] && echo "kept: $WORK" || rm -rf "$WORK"
exit $status
