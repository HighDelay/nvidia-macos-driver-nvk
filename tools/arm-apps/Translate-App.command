#!/bin/bash
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
if [ "$#" -eq 0 ]; then
  app=$(/usr/bin/osascript -e 'POSIX path of (choose file of type {"com.apple.application-bundle"} with prompt "Choose an app to translate. The original will be kept.")') || exit 0
  exec "$HERE/ARM-Apps.command" translate "$app"
fi
exec "$HERE/ARM-Apps.command" translate "$@"
