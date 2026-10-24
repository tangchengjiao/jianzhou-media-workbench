#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-2.1.0-context-20260808}"
ARCH="$(uname -m)"
BUILD_ROOT="${TMPDIR:-/tmp}/jianzhou-release-build-${ARCH}-$(date +%Y%m%d%H%M%S)"
VENV="$BUILD_ROOT/venv"
DIST="$BUILD_ROOT/dist"
WORK="$BUILD_ROOT/work"
OUT="$ROOT/output/releases/见舟工作台-${VERSION}-macos-${ARCH}"

mkdir -p "$OUT"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --disable-pip-version-check --index-url https://pypi.org/simple -r "$ROOT/requirements.lock.txt"
"$VENV/bin/python" -m PyInstaller "$ROOT/packaging/pyinstaller.spec" --noconfirm --clean --distpath "$DIST" --workpath "$WORK"

test -d "$DIST/见舟工作台.app"
ditto -c -k --sequesterRsrc --keepParent "$DIST/见舟工作台.app" "$OUT/见舟工作台-${VERSION}-macos-${ARCH}.zip"
shasum -a 256 "$OUT/见舟工作台-${VERSION}-macos-${ARCH}.zip" > "$OUT/SHA256SUMS.txt"
printf '%s\n' "$OUT"
