#!/bin/bash
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
exec "$HERE/ARM-Apps.command" install "$@"
