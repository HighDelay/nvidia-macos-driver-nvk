#!/bin/bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "$(id -u)" -eq 0 ]; then
  exec /bin/bash "$HERE/install.sh" --payload "$HERE" "$@"
fi
exec /usr/bin/sudo /bin/bash "$HERE/install.sh" --payload "$HERE" "$@"
