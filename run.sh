#!/usr/bin/env bash
# One-command launcher: creates the virtualenv if needed, installs deps, starts the web app.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt

if ! command -v stockfish >/dev/null 2>&1 && [ ! -x /opt/homebrew/bin/stockfish ]; then
  echo "⚠  Stockfish not found. Install it with:  brew install stockfish"
fi

exec .venv/bin/python server.py "$@"
