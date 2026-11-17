#!/bin/sh
# Start the Sia + Plex dashboard (same pattern as CP Live's start script).
# Usage: ./start.sh [--mock]
cd "$(dirname "$0")" || exit 1
exec python3 app.py "$@"
