#!/usr/bin/env bash
set -e
echo ''
echo '  ========================================'
echo '   IdeaForge — Starting GymTracker'
echo '  ========================================'
echo ''

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/backend"

echo '[1/3] Installing dependencies...'
pip install -r requirements.txt --quiet --disable-pip-version-check
echo '      Dependencies OK'
echo ''

echo '[2/3] Starting backend on port 8100...'
echo '      URL: http://127.0.0.1:8100'
echo '      Press Ctrl+C to stop'
echo ''

(sleep 2 && python -m webbrowser http://127.0.0.1:8100 2>/dev/null) &
echo '[3/3] Browser will open shortly...'

uvicorn main:app --host 127.0.0.1 --port 8100 --reload
