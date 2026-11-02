#!/bin/bash
# Build only the real Intel-panel framebuffer for the optional package payload.
set -euo pipefail
BASE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:?output directory}
/bin/bash "$BASE/kexts/NMIntelFB/build.sh" "$OUT" real
KEXT="$OUT/NMIntelFB.kext"
lipo "$KEXT/Contents/MacOS/NMIntelFB" -verify_arch x86_64
codesign --force --sign - --timestamp=none "$KEXT"
codesign --verify --strict "$KEXT"
