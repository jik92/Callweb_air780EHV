#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
if [[ "${1:-start}" == "start" || "${1:-start}" == "restart" ]]; then
  uv sync --project backend
  if [[ ! -d frontend/node_modules ]]; then npm ci --prefix frontend; fi
fi
uv run --project backend python scripts/manage_console.py "${1:-start}"
