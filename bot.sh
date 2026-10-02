#!/bin/sh
# Use native Apple Silicon Python even when launched from a Rosetta terminal.
set -eu
BOT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$BOT_ROOT"
if [ "$(uname -s)" = "Darwin" ] && [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" = "1" ]; then
    exec /usr/bin/arch -arm64 "$BOT_ROOT/.venv/bin/python" "$BOT_ROOT/main.py" "$@"
fi
exec "$BOT_ROOT/.venv/bin/python" "$BOT_ROOT/main.py" "$@"
