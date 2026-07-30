#!/usr/bin/env bash
# One-command setup. Run this, then fill in .env.
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3.11}"
if ! command -v "$PY" >/dev/null 2>&1; then
  PY=python3
fi

echo "==> Using $($PY --version)"

if [ ! -d .venv ]; then
  echo "==> Creating .venv"
  "$PY" -m venv .venv
fi

echo "==> Installing dependencies"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt

if [ ! -x bin/TwitchDownloaderCLI ]; then
  ./scripts/install_twitchdownloader.sh
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from .env.example — fill in your keys."
fi

echo
echo "Done. Next:"
echo "  1. Edit .env with your API keys"
echo "  2. source .venv/bin/activate"
echo "  3. python scripts/check_env.py"
