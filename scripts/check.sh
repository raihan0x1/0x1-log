#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Checking shell scripts"
bash -n scripts/*.sh
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh
else
  echo "  (shellcheck not installed locally; syntax verified with bash -n)"
fi

echo "==> Checking dashboard JavaScript"
if command -v node >/dev/null 2>&1; then
  node --check ox1log/dashboard/static/app.js
else
  echo "  (node not installed locally; skipping app.js syntax check)"
fi

echo "==> Running test suite"
if [[ -x ".venv/bin/pytest" ]]; then
  .venv/bin/pytest
elif command -v uv >/dev/null 2>&1; then
  uv run --no-sync --no-build --with pytest pytest
elif command -v pytest >/dev/null 2>&1; then
  pytest
else
  python3 -m pytest
fi

echo "All checks passed."
