#!/bin/bash
# stage_boost.sh <pkgroot> <nvmtl-boost binary built by tools/nvmtl-boost/build.sh>: put the opt-in GPU boost daemon into a
# package tree (boost_daemon.sh writes the LaunchDaemon plist itself). Nothing installs it by default; the user runs
#   sudo env NULLMOTH_GPU_BOOST=1 ./install.sh
set -euo pipefail
ROOT=${1:?pkgroot}; BIN=${2:?nvmtl-boost binary}
HERE=$(cd "$(dirname "$0")" && pwd)
lipo -archs "$BIN" | grep -qx x86_64 || { echo "STOP: nvmtl-boost is not x86_64"; exit 1; }
REL=Optional/GPU-Boost
mkdir -p "$ROOT/$REL"
cp "$BIN" "$ROOT/$REL/nvmtl-boost"
cp "$HERE/boost_daemon.sh" "$ROOT/$REL/"
cp "$HERE/../docs/GPU-BOOST.md" "$ROOT/$REL/README.md"
codesign -f -s - "$ROOT/$REL/nvmtl-boost"
codesign --verify --strict "$ROOT/$REL/nvmtl-boost"
bash "$HERE/sums_refresh.sh" "$ROOT" "$REL/nvmtl-boost" "$REL/boost_daemon.sh" "$REL/README.md"
echo "staged the GPU boost daemon $(shasum -a 256 "$ROOT/$REL/nvmtl-boost" | cut -c1-8)"
