#!/bin/bash
set -eu
PATH=/usr/bin:/bin:/usr/sbin:/sbin; export PATH
HERE=$(cd "$(dirname "$0")" && pwd)
if ! /usr/bin/xcode-select -p >/dev/null 2>&1; then
  echo "Install Apple's Command Line Tools first: xcode-select --install" >&2
  exit 1
fi
exec /usr/bin/python3 "$HERE/arm_apps.py" "$@"
