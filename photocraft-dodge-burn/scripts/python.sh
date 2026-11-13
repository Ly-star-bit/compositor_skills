#!/bin/bash
# Prints the path of a Python that has numpy and OpenCV, which pcraft_dodge_burn.py needs.
#   PY="$(bash scripts/python.sh)" && "$PY" scripts/pcraft_dodge_burn.py ...
# $PYTHON, python3 or python is used when it already has both. Otherwise a private one is made once, in a virtual
# environment in a cache folder (about 150 MB, needs the network that one time; the one photocraft-liquify makes, so
# the two skills share it); nothing else on the computer is changed. Messages go to stderr; stdout is only the path.
set -euo pipefail

has_libraries() { "$1" -c "import cv2, numpy" > /dev/null 2>&1; }

for candidate in "${PYTHON:-}" python3 python; do
    if [ -n "$candidate" ] && command -v "$candidate" > /dev/null 2>&1 && has_libraries "$candidate"; then
        command -v "$candidate"
        exit 0
    fi
done

case "$(uname -s)" in
    Darwin) cache="$HOME/Library/Caches/photocraft-liquify" ;;
    *) cache="${XDG_CACHE_HOME:-$HOME/.cache}/photocraft-liquify" ;;
esac
venv="$cache/venv"
if [ ! -x "$venv/bin/python" ] || ! has_libraries "$venv/bin/python"; then
    base="$(command -v python3 || command -v python || true)"
    if [ -z "$base" ]; then
        echo "no python3 on this computer: install Python 3.9 or newer, then run this again" >&2
        exit 1
    fi
    echo "[STEP] making a private Python with numpy and OpenCV in $venv (once)" >&2
    mkdir -p "$cache"
    rm -rf "$venv"
    "$base" -m venv "$venv" >&2
    "$venv/bin/python" -m pip install --disable-pip-version-check -q "opencv-python-headless<5" numpy >&2
fi
if ! has_libraries "$venv/bin/python"; then
    echo "could not install numpy and OpenCV into $venv: check the network, or install them yourself with" >&2
    echo "  python3 -m pip install \"opencv-python-headless<5\" numpy" >&2
    exit 1
fi
echo "$venv/bin/python"
