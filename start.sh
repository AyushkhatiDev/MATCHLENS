#!/usr/bin/env bash
set -euo pipefail
MATCHLENS_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ ! -x "$MATCHLENS_ROOT/.venv/bin/python" ]]; then
  echo 'Missing .venv. Install the project dependencies first; see README.md.' >&2
  exit 1
fi
exec "$MATCHLENS_ROOT/.venv/bin/python" "$MATCHLENS_ROOT/scripts/start_project.py" "$@"
