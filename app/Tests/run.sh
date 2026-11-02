#!/bin/bash
# Tests for the Compositor 工具 app, all headless: nothing opens a window, the app itself is never launched.
#
#   app/Tests/run.sh               the logic tests (offline, seconds)
#   app/Tests/run.sh e2e           also end-to-end: builds the Python environment from the internet into a scratch
#                                  folder, as the first launch does, then runs real batches (a few minutes)
#   SUPPORT=<folder>               reuse an environment from an earlier e2e run instead of a fresh one
#   KEEP=1                         keep the work folder (projects, renders, check images)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/.."
WORK="$(mktemp -d "${TMPDIR:-/tmp}/retouch-studio-tests.XXXXXX")"
[ "${KEEP:-0}" = 1 ] && echo "work folder: $WORK" || trap 'rm -rf "$WORK"' EXIT
SOURCES=()
for source in "$APP"/Sources/*.swift; do [ "$(basename "$source")" = App.swift ] || SOURCES+=("$source"); done

build() {  # build <Test>: the test's own main with the app's sources
    swiftc -swift-version 5 -target arm64-apple-macos26.0 -parse-as-library -o "$WORK/$1" "${SOURCES[@]}" "$HERE/$1.swift"
}

status=0
echo "== building the logic tests"
build LogicTests && "$WORK/LogicTests" || status=1

if [ "${1:-}" = e2e ]; then
    PHOTOS="$HOME/Library/Caches/compositor-retouch/tests/data"
    mkdir -p "$PHOTOS"
    for id in S92-40463 jsc2020e032979; do  # public-domain NASA portraits, shared with the skill's tests
        [ -s "$PHOTOS/$id.jpg" ] || curl -sS -m 300 -f -o "$PHOTOS/$id.jpg" "https://images-assets.nasa.gov/image/$id/$id~large.jpg" \
            || { echo "could not download the test photos"; exit 2; }
    done
    echo "== building the end-to-end test"
    build EndToEnd && "$WORK/EndToEnd" "${SUPPORT:-$WORK/support}" "$WORK/e2e" "$PHOTOS/S92-40463.jpg" "$PHOTOS/jsc2020e032979.jpg" \
        || status=1
fi
[ $status = 0 ] && echo "ALL PASSED" || echo "SOME FAILED"
exit $status
